package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"
)

// The journal is the service's only state. It is an append-only JSON-lines file,
// fsynced after every entry, so a reading accepted from readings.jsonl survives
// a crash, a reboot, or an internet / Fabric outage of any length. On start the
// journal is replayed to rebuild every reading's state.
//
// Per reading the entries are, in order:
//
//	queued     the submission (reading ID, sensor, WriteSensor arguments) and the source offset
//	endorsed   transaction ID and the signed transaction bytes (reused for every resubmission)
//	submitted  the signed commit-status request bytes returned by the orderer submission
//	committed  final: commit status VALID, block number
//	invalid    commit status other than VALID: the next attempt endorses a new transaction
//	rejected   final: the reading can never be written (policy, validation, local check)
//	attempt    a transient failure (outage); informational
type Entry struct {
	At         time.Time   `json:"at"`
	Event      string      `json:"event"`
	ReadingID  string      `json:"reading_id"`
	Submission *Submission `json:"submission,omitempty"`
	Offset     int64       `json:"source_offset,omitempty"`
	TxID       string      `json:"tx_id,omitempty"`
	TxBytes    []byte      `json:"tx_bytes,omitempty"`
	Commit     []byte      `json:"commit_bytes,omitempty"`
	Code       string      `json:"code,omitempty"`
	Block      uint64      `json:"block,omitempty"`
	Phase      string      `json:"phase,omitempty"`
	Error      string      `json:"error,omitempty"`
}

// Reading is the replayed state of one reading.
type Reading struct {
	Sub      Submission
	Seq      int // order of arrival
	TxID     string
	TxBytes  []byte
	Commit   []byte
	Final    string // "", "committed", "rejected"
	Code     string
	Block    uint64
	Attempts int
	TxCount  int // transactions endorsed for this reading
}

// Receipt joins the reading ID, the transaction ID and the commit result: one line
// per reading in receipts.jsonl, written when the reading reaches a final state.
type Receipt struct {
	ReadingID    string    `json:"reading_id"`
	SensorID     string    `json:"sensor_id"`
	TxID         string    `json:"tx_id"`
	CommitStatus string    `json:"commit_status"` // VALID, or REJECTED
	Block        uint64    `json:"block_number,omitempty"`
	Reason       string    `json:"reason,omitempty"`
	Submitter    string    `json:"submitter"`
	Transactions int       `json:"transactions_endorsed"`
	FinalAt      time.Time `json:"final_at"`
}

type Journal struct {
	dir      string
	f        *os.File
	receipts *os.File
	State    map[string]*Reading
	Offset   int64 // first byte of readings.jsonl not yet queued
	next     int
	now      func() time.Time
}

func OpenJournal(dir string, now func() time.Time) (*Journal, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	j := &Journal{dir: dir, State: map[string]*Reading{}, now: now}
	path := filepath.Join(dir, "journal.jsonl")
	if data, err := os.ReadFile(path); err == nil {
		good := 0 // bytes of complete, parseable lines
		for good < len(data) {
			nl := bytes.IndexByte(data[good:], '\n')
			var e Entry
			if nl < 0 || json.Unmarshal(data[good:good+nl], &e) != nil {
				if nl >= 0 && good+nl+1 < len(data) {
					return nil, fmt.Errorf("journal corrupt at byte %d", good)
				}
				// torn final line from a crash mid-write: drop it before appending again
				if err := os.Truncate(path, int64(good)); err != nil {
					return nil, err
				}
				break
			}
			j.apply(e)
			good += nl + 1
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	var err error
	if j.f, err = os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600); err != nil {
		return nil, err
	}
	if j.receipts, err = os.OpenFile(filepath.Join(dir, "receipts.jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644); err != nil {
		return nil, err
	}
	return j, nil
}

func (j *Journal) Close() {
	j.f.Close()
	j.receipts.Close()
}

func (j *Journal) apply(e Entry) {
	r := j.State[e.ReadingID]
	switch e.Event {
	case "queued":
		if r == nil && e.Submission != nil {
			j.State[e.ReadingID] = &Reading{Sub: *e.Submission, Seq: j.next}
			j.next++
		}
		if e.Offset > j.Offset {
			j.Offset = e.Offset
		}
		return
	case "skipped":
		if e.Offset > j.Offset {
			j.Offset = e.Offset
		}
		return
	}
	if r == nil {
		return
	}
	switch e.Event {
	case "endorsed":
		r.TxID, r.TxBytes, r.Commit = e.TxID, e.TxBytes, nil
		r.TxCount++
	case "submitted":
		r.Commit = e.Commit
	case "committed":
		r.Final, r.Code, r.Block = "committed", e.Code, e.Block
	case "invalid":
		r.TxBytes, r.Commit, r.Code = nil, nil, e.Code
	case "rejected":
		r.Final, r.Code = "rejected", e.Error
	case "attempt":
		r.Attempts++
	}
}

// Append writes, fsyncs and applies one entry.
func (j *Journal) Append(e Entry) error {
	e.At = j.now().UTC()
	b, err := json.Marshal(e)
	if err != nil {
		return err
	}
	if _, err := j.f.Write(append(b, '\n')); err != nil {
		return err
	}
	if err := j.f.Sync(); err != nil {
		return err
	}
	j.apply(e)
	return nil
}

func (j *Journal) WriteReceipt(rc Receipt) error {
	rc.FinalAt = j.now().UTC()
	b, _ := json.Marshal(rc)
	if _, err := j.receipts.Write(append(b, '\n')); err != nil {
		return err
	}
	return j.receipts.Sync()
}

// Pending returns readings without a final state, oldest first.
func (j *Journal) Pending() []*Reading {
	var out []*Reading
	for _, r := range j.State {
		if r.Final == "" {
			out = append(out, r)
		}
	}
	sort.Slice(out, func(a, b int) bool { return out[a].Seq < out[b].Seq })
	return out
}
