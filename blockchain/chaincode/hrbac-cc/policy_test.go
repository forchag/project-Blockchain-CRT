package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Illustrative roster of the example gateway ex-gw-north (config/deployment.example.yaml).
const northRoster = "ex-sns-001|ex-sns-002|ex-sns-003"

func TestDecideWrite(t *testing.T) {
	cases := []struct {
		name, role, enroll, sensors, sensor string
		wantErr                             string
	}{
		{"sensor writes its own reading", "Sensor", "ex-sns-001", "", "ex-sns-001", ""},
		{"sensor writes another sensor's reading", "Sensor", "ex-sns-001", "", "ex-sns-002", "ROLE_INSUFFICIENT"},
		{"gateway writes a delegated sensor", "Gateway", "ex-gw-north", northRoster, "ex-sns-002", ""},
		{"gateway writes first and last roster entries", "Gateway", "ex-gw-north", northRoster, "ex-sns-003", ""},
		{"gateway writes a sensor of another zone", "Gateway", "ex-gw-north", northRoster, "ex-sns-004", "SENSOR_NOT_DELEGATED"},
		{"gateway with no sensors attribute", "Gateway", "ex-gw-north", "", "ex-sns-001", "SENSOR_NOT_DELEGATED"},
		{"prefix is not a match", "Gateway", "ex-gw-north", "ex-sns-00", "ex-sns-001", "SENSOR_NOT_DELEGATED"},
		{"gateway cannot use its own ID as a sensor", "Gateway", "ex-gw-north", northRoster, "ex-gw-north", "SENSOR_NOT_DELEGATED"},
		{"farmer cannot write", "Farmer", "ex-usr-001", "", "ex-sns-001", "OP_NOT_PERMITTED"},
		{"admin cannot write a sensor's reading", "Admin", "ex-usr-900", "", "ex-sns-001", "OP_NOT_PERMITTED"},
		{"no role", "", "x", "", "ex-sns-001", "OP_NOT_PERMITTED"},
		{"empty sensor ID", "Sensor", "", "", "", "empty sensorID"},
	}
	for _, c := range cases {
		err := decideWrite(c.role, c.enroll, c.sensors, c.sensor)
		if c.wantErr == "" && err != nil {
			t.Errorf("%s: unexpected error %v", c.name, err)
		}
		if c.wantErr != "" && (err == nil || !strings.Contains(err.Error(), c.wantErr)) {
			t.Errorf("%s: want error containing %q, got %v", c.name, c.wantErr, err)
		}
	}
}

func TestPermissionTableAllowsGatewayWrites(t *testing.T) {
	if scoped, ok := permissions["Gateway"]["WriteSensor"]; !ok || !scoped {
		t.Fatal("Gateway must hold a zone-scoped WriteSensor permission")
	}
	if _, ok := permissions["Gateway"]["ReadSensorData"]; ok {
		t.Fatal("Gateway must not read sensor data")
	}
}

func TestBuildRecord(t *testing.T) {
	in := `{"captured_at":"2030-01-15T00:30:01.250Z","soil_moisture":41.4,"temp_c":25.3,"humidity_pct":64.3,"ph":null,"light_lux":1500,"quality":{"ph":"invalid_at_node"}}`
	b, err := buildRecord(in, "ex-sns-017", "R017-0470-20300115", "3,3,3,3,3", "ex-gw-east", "Gateway")
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	if m["ph"] != nil {
		t.Errorf("a missing quantity must stay null, got %v", m["ph"])
	}
	if m["submitter"] != "ex-gw-east" || m["submitter_role"] != "Gateway" || m["sensor_id"] != "ex-sns-017" {
		t.Errorf("attribution fields wrong: %v", m)
	}
	// deterministic bytes across endorsers
	b2, _ := buildRecord(in, "ex-sns-017", "R017-0470-20300115", "3,3,3,3,3", "ex-gw-east", "Gateway")
	if string(b) != string(b2) {
		t.Error("record bytes are not deterministic")
	}
	// absent keys become explicit nulls, never zeros
	b3, err := buildRecord(`{"captured_at":"2030-01-15T00:30:01Z","temp_c":20}`, "ex-sns-001", "R1", "", "ex-sns-001", "Sensor")
	if err != nil || !strings.Contains(string(b3), `"soil_moisture":null`) {
		t.Errorf("absent quantity not stored as null: %s %v", b3, err)
	}
}

func TestBuildRecordRejects(t *testing.T) {
	bad := map[string]string{
		"not JSON":             `{`,
		"no timestamp":         `{"temp_c":20}`,
		"out of range":         `{"captured_at":"t","temp_c":120}`,
		"string value":         `{"captured_at":"t","temp_c":"20"}`,
		"no quantity":          `{"captured_at":"t","ph":null}`,
		"spoofed submitter":    `{"captured_at":"t","temp_c":20,"submitter":"ex-sns-001"}`,
		"negative illuminance": `{"captured_at":"t","light_lux":-1}`,
	}
	for name, in := range bad {
		if _, err := buildRecord(in, "ex-sns-001", "R1", "", "ex-gw-north", "Gateway"); err == nil {
			t.Errorf("%s: accepted %s", name, in)
		}
	}
	if _, err := buildRecord(`{"captured_at":"t","temp_c":20}`, "ex-sns-001", "", "", "x", "Sensor"); err == nil {
		t.Error("empty readingID accepted")
	}
}

// The gateway's Ed25519 record signature (gateway/gateway_service.py) is stored unchanged, so a
// ledger record can be tied back to the gateway's signed reading. The chaincode does not verify it.
func TestBuildRecordKeepsGatewaySignature(t *testing.T) {
	in := `{"captured_at":"2030-01-15T00:30:01Z","temp_c":21.5,` +
		`"gateway_signature":{"alg":"Ed25519","key_id":"0011223344556677","sha256":"ab","sig":"cd"}}`
	b, err := buildRecord(in, "ex-sns-002", "R002-0031-20300115", "3,3,3,3,3", "ex-gw-north", "Gateway")
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	sig, ok := m["gateway_signature"].(map[string]any)
	if !ok || sig["alg"] != "Ed25519" || sig["key_id"] != "0011223344556677" || sig["sha256"] != "ab" || sig["sig"] != "cd" {
		t.Fatalf("gateway_signature not stored unchanged: %v", m["gateway_signature"])
	}
}

// examples/example_transactions.json: every illustrative submission gets the decision it documents.
// decideWrite + buildRecord are exactly what WriteSensor runs after reading the caller's attributes.
func TestExampleTransactions(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "examples", "example_transactions.json"))
	if err != nil {
		t.Skipf("examples not available: %v", err)
	}
	var doc struct {
		Transactions []struct {
			TxID     string `json:"txid"`
			Identity struct {
				EnrollmentID string `json:"enrollment_id"`
				Role         string `json:"role"`
				Sensors      string `json:"sensors"`
			} `json:"identity"`
			Args struct {
				SensorID  string         `json:"sensor_id"`
				ReadingID string         `json:"reading_id"`
				Residues  string         `json:"residues"`
				Reading   map[string]any `json:"reading"`
			} `json:"args"`
			Expected struct {
				Decision      string `json:"decision"`
				Error         string `json:"error"`
				Submitter     string `json:"submitter"`
				SubmitterRole string `json:"submitter_role"`
			} `json:"expected"`
		} `json:"transactions"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	if len(doc.Transactions) == 0 {
		t.Fatal("no example transactions")
	}
	for _, tx := range doc.Transactions {
		id := tx.Identity
		err := decideWrite(id.Role, id.EnrollmentID, id.Sensors, tx.Args.SensorID)
		var rec []byte
		if err == nil {
			reading, _ := json.Marshal(tx.Args.Reading)
			rec, err = buildRecord(string(reading), tx.Args.SensorID, tx.Args.ReadingID, tx.Args.Residues, id.EnrollmentID, id.Role)
		}
		switch tx.Expected.Decision {
		case "allowed":
			if err != nil {
				t.Errorf("%s: want allowed, got %v", tx.TxID, err)
				continue
			}
			var m map[string]any
			json.Unmarshal(rec, &m)
			if m["submitter"] != tx.Expected.Submitter || m["submitter_role"] != tx.Expected.SubmitterRole {
				t.Errorf("%s: attribution %v / %v", tx.TxID, m["submitter"], m["submitter_role"])
			}
		case "denied":
			if err == nil || !strings.Contains(err.Error(), tx.Expected.Error) {
				t.Errorf("%s: want denial containing %q, got %v", tx.TxID, tx.Expected.Error, err)
			}
		default:
			t.Errorf("%s: unknown expected decision %q", tx.TxID, tx.Expected.Decision)
		}
	}
}
