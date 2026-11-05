package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------- fake Fabric

// fakeLedger keeps a world state keyed like the chaincode (sensor, reading) and,
// like Fabric, commits a transaction ID at most once.
type fakeLedger struct {
	world     map[string]string // reading key -> reading JSON
	committed map[string]string // txID -> validation code
	blocks    uint64
	n         int
	// fail[phase] = remaining failures to inject; ambiguous submit = ordered but error returned
	fail          map[string]int
	ambiguousOnce bool
	invalidOnce   bool
	deny          string // chaincode denial message for every endorsement
	calls         map[string]int
}

func newFake() *fakeLedger {
	return &fakeLedger{world: map[string]string{}, committed: map[string]string{}, fail: map[string]int{}, calls: map[string]int{}}
}

var errUnavailable = errors.New("rpc error: code = Unavailable desc = connection refused")

type fakeTx struct{ TxID, Key, Value string }

func (f *fakeLedger) Endorse(_ context.Context, args []string) (string, []byte, error) {
	f.calls["endorse"]++
	if f.fail["endorse"] > 0 {
		f.fail["endorse"]--
		return "", nil, errUnavailable
	}
	if f.deny != "" {
		return "", nil, classifyChaincodeError("endorse: chaincode response 500, " + f.deny)
	}
	f.n++
	tx := fakeTx{TxID: fmt.Sprintf("tx%04d", f.n), Key: args[0] + "~" + args[1], Value: args[3]}
	b, _ := json.Marshal(tx)
	return tx.TxID, b, nil
}

func (f *fakeLedger) order(tx fakeTx) {
	if _, dup := f.committed[tx.TxID]; dup {
		return // DUPLICATE_TXID: the repeat is not applied
	}
	f.blocks++
	if f.invalidOnce {
		f.invalidOnce = false
		f.committed[tx.TxID] = "MVCC_READ_CONFLICT"
		return
	}
	f.committed[tx.TxID] = "VALID"
	f.world[tx.Key] = tx.Value
}

func (f *fakeLedger) Submit(_ context.Context, b []byte) ([]byte, error) {
	f.calls["submit"]++
	var tx fakeTx
	if err := json.Unmarshal(b, &tx); err != nil {
		return nil, ErrPermanent{Reason: "bad tx"}
	}
	if f.fail["submit"] > 0 {
		f.fail["submit"]--
		return nil, errUnavailable
	}
	f.order(tx)
	if f.ambiguousOnce { // the orderer took it, but the reply was lost
		f.ambiguousOnce = false
		return nil, errors.New("rpc error: code = DeadlineExceeded desc = context deadline exceeded")
	}
	return []byte(tx.TxID), nil
}

func (f *fakeLedger) Status(_ context.Context, commit []byte) (string, uint64, error) {
	f.calls["status"]++
	if f.fail["status"] > 0 {
		f.fail["status"]--
		return "", 0, errUnavailable
	}
	code, ok := f.committed[string(commit)]
	if !ok {
		return "", 0, errors.New("rpc error: code = DeadlineExceeded desc = waiting for commit")
	}
	return code, f.blocks, nil
}

// ---------------------------------------------------------------- helpers

func fptr(v float64) *float64 { return &v }

func reading(node, seq int, at time.Time) GatewayReading {
	q := []string{"soil_moisture", "temperature", "humidity", "ph", "illuminance"}
	vals := []float64{41.4, 25.299999, 64.3, 6.8200001, 1500.0000001}
	g := GatewayReading{GatewayID: "ex-gw-north", NodeID: node, SensorID: fmt.Sprintf("ex-sns-%03d", node),
		DeviceID: fmt.Sprintf("ex-node-%03d", node), Seq: seq, ReceivedAtMs: at.UnixMilli(),
		Values: map[string]*float64{}, Status: map[string]string{}, ResiduesReceived: map[string]int{}}
	for i, n := range q {
		g.Values[n], g.Status[n], g.ResiduesReceived[n] = fptr(vals[i]), "reconstructed", 3
	}
	return g
}

var t0 = time.Date(2030, 1, 15, 0, 30, 1, 250e6, time.UTC)

func northPolicy() Policy {
	p := Policy{EnrollmentID: "ex-gw-north", Role: "Gateway", Zone: "North", Sensors: map[string]bool{}}
	for i := 1; i <= 13; i++ {
		p.Sensors[fmt.Sprintf("ex-sns-%03d", i)] = true
	}
	return p
}

type env struct {
	t        *testing.T
	dir      string
	readings string
	ledger   *fakeLedger
	r        *Runner
}

func newEnv(t *testing.T) *env {
	dir := t.TempDir()
	e := &env{t: t, dir: dir, readings: filepath.Join(dir, "readings.jsonl"), ledger: newFake()}
	e.reopen()
	return e
}

// reopen simulates a process restart: a new journal replayed from disk.
func (e *env) reopen() {
	if e.r != nil {
		e.r.J.Close()
	}
	j, err := OpenJournal(filepath.Join(e.dir, "state"), func() time.Time { return t0 })
	if err != nil {
		e.t.Fatal(err)
	}
	e.r = &Runner{J: j, L: e.ledger, Policy: northPolicy(), Timeout: time.Second, Log: e.t.Logf, Readings: e.readings}
}

func (e *env) write(rs ...GatewayReading) {
	f, err := os.OpenFile(e.readings, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		e.t.Fatal(err)
	}
	defer f.Close()
	for _, r := range rs {
		b, _ := json.Marshal(r)
		f.Write(append(b, '\n'))
	}
}

func (e *env) cycle() error {
	if _, err := e.r.Ingest(); err != nil {
		e.t.Fatal(err)
	}
	_, err := e.r.Drain(context.Background())
	return err
}

func (e *env) receipts() []Receipt {
	b, _ := os.ReadFile(filepath.Join(e.dir, "state", "receipts.jsonl"))
	var out []Receipt
	for _, l := range strings.Split(strings.TrimSpace(string(b)), "\n") {
		if l == "" {
			continue
		}
		var r Receipt
		json.Unmarshal([]byte(l), &r)
		out = append(out, r)
	}
	return out
}

// ---------------------------------------------------------------- mapping

func TestMappingFieldsUnitsAndNulls(t *testing.T) {
	g := reading(17, 470, t0)
	g.Values["ph"], g.Status["ph"] = nil, "invalid_at_node"
	g.Status["humidity"] = "abandoned" // value present but not reconstructed: must not be sent
	g.ResiduesReceived["humidity"] = 1
	s, err := ToSubmission(g)
	if err != nil {
		t.Fatal(err)
	}
	if s.ReadingID != "R017-0470-20300115" || s.SensorID != "ex-sns-017" || s.Residues != "3,3,1,3,3" {
		t.Fatalf("ids/residues: %+v", s)
	}
	var m map[string]any
	json.Unmarshal([]byte(s.ReadingJSON), &m)
	if m["temp_c"] != 25.3 || m["soil_moisture"] != 41.4 || m["light_lux"] != 1500.0 {
		t.Errorf("rounded values wrong: %v", m)
	}
	if m["ph"] != nil || m["humidity_pct"] != nil {
		t.Errorf("non-reconstructed quantities must be null: ph=%v humidity=%v", m["ph"], m["humidity_pct"])
	}
	if m["captured_at"] != "2030-01-15T00:30:01.250Z" {
		t.Errorf("captured_at %v", m["captured_at"])
	}
	q := m["quality"].(map[string]any)
	if q["ph"] != "invalid_at_node" || q["humidity_pct"] != "abandoned" || q["temp_c"] != "reconstructed" {
		t.Errorf("quality %v", q)
	}
	s2, _ := ToSubmission(g)
	if s2.ReadingJSON != s.ReadingJSON {
		t.Error("conversion is not deterministic")
	}
}

func TestMappingCarriesGatewaySignature(t *testing.T) {
	g := reading(2, 31, t0)
	g.GatewaySignature = map[string]any{"alg": "Ed25519", "key_id": "0011223344556677",
		"sha256": "illustrative-digest", "sig": "illustrative-signature"}
	s, err := ToSubmission(g)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	json.Unmarshal([]byte(s.ReadingJSON), &m)
	sig, ok := m["gateway_signature"].(map[string]any)
	if !ok || sig["alg"] != "Ed25519" || sig["key_id"] != "0011223344556677" {
		t.Fatalf("signature not carried into the ledger document: %v", m["gateway_signature"])
	}
	g.GatewaySignature = nil
	s2, _ := ToSubmission(g)
	if strings.Contains(s2.ReadingJSON, "gateway_signature") {
		t.Error("unsigned reading must not get an empty signature field")
	}
}

func TestMappingRejectsUnusableReadings(t *testing.T) {
	empty := reading(1, 1, t0)
	for k := range empty.Status {
		empty.Status[k] = "abandoned"
	}
	if _, err := ToSubmission(empty); err == nil {
		t.Error("reading with no reconstructed quantity accepted")
	}
	bad := reading(1, 1, t0)
	bad.SensorID = ""
	if _, err := ToSubmission(bad); err == nil {
		t.Error("reading without sensor accepted")
	}
}

func TestReadingIDSurvivesSequenceWrap(t *testing.T) {
	a := ReadingID(reading(5, 12, t0))
	b := ReadingID(reading(5, 12, t0.Add(65536*30*time.Minute))) // same seq after wrap-around
	if a == b {
		t.Fatalf("wrapped sequence reuses ID %s", a)
	}
}

// ---------------------------------------------------------------- happy path and receipts

func TestCommitAndReceipt(t *testing.T) {
	e := newEnv(t)
	e.write(reading(1, 10, t0), reading(2, 10, t0))
	if err := e.cycle(); err != nil {
		t.Fatal(err)
	}
	rc := e.receipts()
	if len(rc) != 2 || rc[0].CommitStatus != "VALID" || rc[0].TxID == "" || rc[0].ReadingID != "R001-0010-20300115" ||
		rc[0].Submitter != "ex-gw-north" || rc[0].Block == 0 {
		t.Fatalf("receipts %+v", rc)
	}
	if len(e.ledger.world) != 2 || len(e.r.J.Pending()) != 0 {
		t.Fatalf("world %d pending %d", len(e.ledger.world), len(e.r.J.Pending()))
	}
}

// ---------------------------------------------------------------- outages and restarts

func TestOutageKeepsReadingsAndOrder(t *testing.T) {
	e := newEnv(t)
	e.write(reading(1, 1, t0), reading(2, 1, t0), reading(3, 1, t0))
	e.ledger.fail["endorse"] = 4 // network down for four attempts
	for i := 0; i < 4; i++ {
		err := e.cycle()
		var o ErrOutage
		if !errors.As(err, &o) || o.ReadingID != "R001-0001-20300115" {
			t.Fatalf("attempt %d: want outage on the oldest reading, got %v", i, err)
		}
		if e.ledger.calls["endorse"] != i+1 {
			t.Fatalf("later readings must wait behind the oldest during an outage")
		}
	}
	e.reopen() // the gateway reboots during the outage
	if len(e.r.J.Pending()) != 3 {
		t.Fatalf("readings lost across restart: %d pending", len(e.r.J.Pending()))
	}
	if err := e.cycle(); err != nil {
		t.Fatal(err)
	}
	rc := e.receipts()
	if len(rc) != 3 || rc[0].ReadingID != "R001-0001-20300115" || rc[2].ReadingID != "R003-0001-20300115" {
		t.Fatalf("receipts out of order or missing: %+v", rc)
	}
}

func TestSubmitOutageResubmitsSameTransaction(t *testing.T) {
	e := newEnv(t)
	e.write(reading(4, 7, t0))
	e.ledger.fail["submit"] = 2
	e.cycle()
	e.cycle()
	if err := e.cycle(); err != nil {
		t.Fatal(err)
	}
	if e.ledger.calls["endorse"] != 1 || e.ledger.calls["submit"] != 3 {
		t.Fatalf("want 1 endorsement reused for 3 submissions, got %v", e.ledger.calls)
	}
	if rc := e.receipts(); len(rc) != 1 || rc[0].TxID != "tx0001" || rc[0].Transactions != 1 {
		t.Fatalf("receipt %+v", rc)
	}
}

func TestAmbiguousSubmitDoesNotDuplicate(t *testing.T) {
	e := newEnv(t)
	e.write(reading(5, 3, t0))
	e.ledger.ambiguousOnce = true // ordered and committed, but the client saw a timeout
	if err := e.cycle(); err == nil {
		t.Fatal("expected an outage report for the lost reply")
	}
	if err := e.cycle(); err != nil {
		t.Fatal(err)
	}
	if len(e.ledger.committed) != 1 || len(e.ledger.world) != 1 {
		t.Fatalf("duplicate transaction or record: committed=%v world=%d", e.ledger.committed, len(e.ledger.world))
	}
	if rc := e.receipts(); len(rc) != 1 || rc[0].TxID != "tx0001" || rc[0].CommitStatus != "VALID" {
		t.Fatalf("receipt %+v", rc)
	}
}

func TestCrashAfterSubmitResumesFromStatus(t *testing.T) {
	e := newEnv(t)
	e.write(reading(6, 9, t0))
	e.ledger.fail["status"] = 1
	if err := e.cycle(); err == nil {
		t.Fatal("expected status outage")
	}
	e.reopen()
	if err := e.cycle(); err != nil {
		t.Fatal(err)
	}
	if e.ledger.calls["endorse"] != 1 || e.ledger.calls["submit"] != 1 {
		t.Fatalf("restart must resume at the status check, not resubmit: %v", e.ledger.calls)
	}
}

func TestInvalidatedTransactionIsReplacedOnce(t *testing.T) {
	e := newEnv(t)
	e.write(reading(7, 2, t0))
	e.ledger.invalidOnce = true
	if err := e.cycle(); err != nil {
		t.Fatal(err)
	}
	rc := e.receipts()
	if len(rc) != 1 || rc[0].TxID != "tx0002" || rc[0].Transactions != 2 || len(e.ledger.world) != 1 {
		t.Fatalf("receipt %+v world %d", rc, len(e.ledger.world))
	}
}

func TestRereadingSourceDoesNotDuplicate(t *testing.T) {
	e := newEnv(t)
	r := reading(8, 4, t0)
	e.write(r)
	e.cycle()
	e.write(r)       // the gateway service wrote the same reading twice
	e.r.J.Offset = 0 // and the offset was lost
	e.cycle()
	if len(e.receipts()) != 1 || e.ledger.calls["endorse"] != 1 {
		t.Fatalf("reading submitted twice: receipts %d calls %v", len(e.receipts()), e.ledger.calls)
	}
}

func TestTornJournalLineIsDropped(t *testing.T) {
	e := newEnv(t)
	e.write(reading(9, 1, t0))
	e.ledger.fail["endorse"] = 1
	e.cycle()
	e.r.J.Close()
	jf := filepath.Join(e.dir, "state", "journal.jsonl")
	f, _ := os.OpenFile(jf, os.O_APPEND|os.O_WRONLY, 0o600)
	f.WriteString(`{"event":"endorsed","reading_id":"R009`) // power cut mid-write
	f.Close()
	e.r = nil
	e.reopen()
	if err := e.cycle(); err != nil {
		t.Fatal(err)
	}
	if len(e.receipts()) != 1 {
		t.Fatal("reading not completed after torn journal line")
	}
}

// ---------------------------------------------------------------- identity policy

func TestLocalPolicyRefusesUndelegatedSensor(t *testing.T) {
	e := newEnv(t)
	e.write(reading(14, 1, t0)) // ex-sns-014 is not on this gateway's roster
	if err := e.cycle(); err != nil {
		t.Fatal(err)
	}
	rc := e.receipts()
	if len(rc) != 1 || rc[0].CommitStatus != "REJECTED" || !strings.Contains(rc[0].Reason, "not delegated") {
		t.Fatalf("receipt %+v", rc)
	}
	if e.ledger.calls["endorse"] != 0 {
		t.Fatal("an undelegated reading must never reach Fabric")
	}
}

func TestChaincodeDenialIsFinalNotRetried(t *testing.T) {
	e := newEnv(t)
	e.write(reading(1, 50, t0))
	e.ledger.deny = "HRBAC denied: SENSOR_NOT_DELEGATED (gateway ex-gw-north is not delegated for ex-sns-001)"
	if err := e.cycle(); err != nil {
		t.Fatal(err)
	}
	e.cycle()
	rc := e.receipts()
	if len(rc) != 1 || rc[0].CommitStatus != "REJECTED" || !strings.Contains(rc[0].Reason, "SENSOR_NOT_DELEGATED") {
		t.Fatalf("receipt %+v", rc)
	}
	if e.ledger.calls["endorse"] != 1 {
		t.Fatalf("a policy denial must not be retried: %v", e.ledger.calls)
	}
}

func certWithAttrs(t *testing.T, cn string, attrs map[string]string) *x509.Certificate {
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	val, _ := json.Marshal(map[string]any{"attrs": attrs})
	tpl := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: cn},
		NotBefore: t0, NotAfter: t0.Add(time.Hour), ExtraExtensions: []pkix.Extension{{Id: attrOID, Value: val}}}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	c, _ := x509.ParseCertificate(der)
	return c
}

func TestPolicyFromCert(t *testing.T) {
	gw, err := PolicyFromCert(certWithAttrs(t, "ex-gw-north", map[string]string{
		"role": "Gateway", "zone": "North", "sensors": "ex-sns-001|ex-sns-002", "hf.EnrollmentID": "ex-gw-north"}))
	if err != nil || gw.Allows("ex-sns-002") != nil || gw.Allows("ex-sns-014") == nil || gw.Allows("ex-gw-north") == nil {
		t.Fatalf("gateway policy: %+v %v", gw, err)
	}
	s, err := PolicyFromCert(certWithAttrs(t, "ex-sns-005", map[string]string{"role": "Sensor", "hf.EnrollmentID": "ex-sns-005"}))
	if err != nil || s.Allows("ex-sns-005") != nil || s.Allows("ex-sns-006") == nil {
		t.Fatalf("sensor policy: %+v %v", s, err)
	}
	if _, err := PolicyFromCert(certWithAttrs(t, "gw-x", map[string]string{"role": "Gateway"})); err == nil {
		t.Error("gateway without sensors accepted")
	}
	if _, err := PolicyFromCert(certWithAttrs(t, "usr-1", map[string]string{"role": "Farmer"})); err == nil {
		t.Error("farmer identity accepted as submitter")
	}
}
