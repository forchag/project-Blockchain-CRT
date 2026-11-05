package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"time"
)

// Ledger is the Fabric side. fabricLedger (fabric.go) implements it with the
// Fabric Gateway SDK; tests use a fake.
type Ledger interface {
	// Endorse builds, signs and endorses a WriteSensor proposal; returns the
	// transaction ID and the signed transaction, ready to submit.
	Endorse(ctx context.Context, args []string) (txID string, tx []byte, err error)
	// Submit sends the signed transaction to the orderer. Resubmitting the same
	// bytes is safe: Fabric commits one transaction ID at most once.
	Submit(ctx context.Context, tx []byte) (commit []byte, err error)
	// Status waits for the transaction's commit status.
	Status(ctx context.Context, commit []byte) (code string, block uint64, err error)
}

// ErrPermanent marks a failure that retrying cannot fix (identity policy,
// validation). Everything else is treated as an outage and retried.
type ErrPermanent struct{ Reason string }

func (e ErrPermanent) Error() string { return e.Reason }

// ErrOutage wraps a transient failure (network, peers, orderers). The reading
// keeps its state; Drain stops and the caller retries after a backoff.
type ErrOutage struct {
	ReadingID, Phase string
	Err              error
}

func (e ErrOutage) Error() string { return e.ReadingID + " " + e.Phase + ": " + e.Err.Error() }
func (e ErrOutage) Unwrap() error { return e.Err }

type Runner struct {
	J        *Journal
	L        Ledger
	Policy   Policy // which sensors this identity may submit for
	Timeout  time.Duration
	Log      func(format string, args ...any)
	Readings string // path of the gateway's readings.jsonl
}

// Ingest queues every complete line of readings.jsonl past the journal offset.
// A reading already in the journal is never queued twice, so re-reading the
// file (offset lost, file rotated back) cannot duplicate a submission.
func (r *Runner) Ingest() (int, error) {
	f, err := os.Open(r.Readings)
	if errors.Is(err, os.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return 0, err
	}
	if st.Size() < r.J.Offset { // file was truncated or replaced: rescan from the start
		r.J.Offset = 0
	}
	if _, err := f.Seek(r.J.Offset, io.SeekStart); err != nil {
		return 0, err
	}
	br := bufio.NewReader(f)
	off, n := r.J.Offset, 0
	for {
		line, err := br.ReadBytes('\n')
		if err == io.EOF {
			return n, nil // a partial last line is left for the next pass
		}
		if err != nil {
			return n, err
		}
		off += int64(len(line))
		var g GatewayReading
		if jerr := json.Unmarshal(line, &g); jerr != nil {
			r.Log("skip unparsable line ending at byte %d: %v", off, jerr)
			if err := r.J.Append(Entry{Event: "skipped", ReadingID: "-", Offset: off, Error: jerr.Error()}); err != nil {
				return n, err
			}
			continue
		}
		sub, serr := ToSubmission(g)
		id := ReadingID(g)
		if _, seen := r.J.State[id]; seen {
			if err := r.J.Append(Entry{Event: "skipped", ReadingID: id, Offset: off, Error: "already in journal"}); err != nil {
				return n, err
			}
			continue
		}
		if serr == nil {
			serr = r.Policy.Allows(sub.SensorID)
		}
		if serr != nil {
			sub = Submission{ReadingID: id, SensorID: g.SensorID}
		}
		if err := r.J.Append(Entry{Event: "queued", ReadingID: id, Submission: &sub, Offset: off}); err != nil {
			return n, err
		}
		n++
		if serr != nil { // refused locally: never sent to Fabric
			if err := r.reject(r.J.State[id], "local: "+serr.Error()); err != nil {
				return n, err
			}
		}
	}
}

// Drain works through pending readings oldest first. It stops at the first
// outage so that readings stay in arrival order; the caller backs off and calls
// again. Returns the number of readings that reached a final state.
func (r *Runner) Drain(ctx context.Context) (int, error) {
	done := 0
	for _, rd := range r.J.Pending() {
		final, err := r.step(ctx, rd)
		if err != nil {
			return done, err
		}
		if final {
			done++
		}
	}
	return done, nil
}

// step advances one reading as far as it can go: endorse -> submit -> status.
// A non-nil error means an outage; the reading keeps its state for the next pass.
func (r *Runner) step(ctx context.Context, rd *Reading) (bool, error) {
	// endorse, submit, status = 3 phases; up to 5 transactions per reading (see the invalid branch)
	for phase := 0; phase < 16 && rd.Final == ""; phase++ {
		switch {
		case rd.TxBytes == nil:
			c, cancel := context.WithTimeout(ctx, r.Timeout)
			txID, tx, err := r.L.Endorse(c, rd.Sub.Args())
			cancel()
			if err != nil {
				return r.fail(rd, "endorse", err)
			}
			if err := r.J.Append(Entry{Event: "endorsed", ReadingID: rd.Sub.ReadingID, TxID: txID, TxBytes: tx}); err != nil {
				return false, err
			}
		case rd.Commit == nil:
			c, cancel := context.WithTimeout(ctx, r.Timeout)
			commit, err := r.L.Submit(c, rd.TxBytes)
			cancel()
			if err != nil {
				return r.fail(rd, "submit", err) // keep TxBytes: the retry resubmits the same transaction ID
			}
			if err := r.J.Append(Entry{Event: "submitted", ReadingID: rd.Sub.ReadingID, TxID: rd.TxID, Commit: commit}); err != nil {
				return false, err
			}
		default:
			c, cancel := context.WithTimeout(ctx, r.Timeout)
			code, block, err := r.L.Status(c, rd.Commit)
			cancel()
			if err != nil {
				return r.fail(rd, "status", err)
			}
			if code == "VALID" {
				if err := r.J.Append(Entry{Event: "committed", ReadingID: rd.Sub.ReadingID, TxID: rd.TxID, Code: code, Block: block}); err != nil {
					return false, err
				}
				return true, r.J.WriteReceipt(Receipt{ReadingID: rd.Sub.ReadingID, SensorID: rd.Sub.SensorID, TxID: rd.TxID,
					CommitStatus: code, Block: block, Submitter: r.Policy.EnrollmentID, Transactions: rd.TxCount})
			}
			// Committed but invalidated (e.g. MVCC_READ_CONFLICT, ENDORSEMENT_POLICY_FAILURE): the
			// transaction wrote nothing, so a new transaction for the same reading cannot duplicate it.
			r.Log("%s: tx %s committed as %s; endorsing a new transaction", rd.Sub.ReadingID, rd.TxID, code)
			if err := r.J.Append(Entry{Event: "invalid", ReadingID: rd.Sub.ReadingID, TxID: rd.TxID, Code: code}); err != nil {
				return false, err
			}
			if rd.TxCount >= 5 {
				return true, r.reject(rd, fmt.Sprintf("invalidated %d times, last %s", rd.TxCount, code))
			}
		}
	}
	return rd.Final != "", nil
}

func (r *Runner) fail(rd *Reading, phase string, err error) (bool, error) {
	var perm ErrPermanent
	if errors.As(err, &perm) {
		return true, r.reject(rd, phase+": "+perm.Reason)
	}
	if jerr := r.J.Append(Entry{Event: "attempt", ReadingID: rd.Sub.ReadingID, TxID: rd.TxID, Phase: phase, Error: err.Error()}); jerr != nil {
		return false, jerr
	}
	r.Log("%s: %s failed, kept for retry: %v", rd.Sub.ReadingID, phase, err)
	return false, ErrOutage{ReadingID: rd.Sub.ReadingID, Phase: phase, Err: err}
}

func (r *Runner) reject(rd *Reading, reason string) error {
	if err := r.J.Append(Entry{Event: "rejected", ReadingID: rd.Sub.ReadingID, TxID: rd.TxID, Error: reason}); err != nil {
		return err
	}
	r.Log("%s: rejected: %s", rd.Sub.ReadingID, reason)
	return r.J.WriteReceipt(Receipt{ReadingID: rd.Sub.ReadingID, SensorID: rd.Sub.SensorID, TxID: rd.TxID,
		CommitStatus: "REJECTED", Reason: reason, Submitter: r.Policy.EnrollmentID, Transactions: rd.TxCount})
}

// classifyChaincodeError: an endorsement refused by the chaincode for policy or
// validation reasons is permanent; anything else (no peers, timeouts) is an outage.
func classifyChaincodeError(msg string) error {
	for _, p := range []string{"HRBAC denied", "malformed reading"} {
		if i := strings.Index(msg, p); i >= 0 {
			return ErrPermanent{Reason: msg[i:]}
		}
	}
	return nil
}
