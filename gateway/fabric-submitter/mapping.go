package main

import (
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"
)

// GatewayReading is one line of the gateway MAC service's readings.jsonl
// (gateway/gateway_service.py, CompletedReading).
type GatewayReading struct {
	GatewayID        string              `json:"gateway_id"`
	NodeID           int                 `json:"node_id"`
	SensorID         string              `json:"sensor_id"`
	DeviceID         string              `json:"device_id"`
	Seq              int                 `json:"seq"`
	Event            bool                `json:"event"`
	ReceivedAtMs     int64               `json:"received_at_ms"`
	Values           map[string]*float64 `json:"values"`
	Status           map[string]string   `json:"status"`
	ResiduesReceived map[string]int      `json:"residues_received"`
	Retries          int                 `json:"retries"`
	// Ed25519 signature written by gateway_service.py over the canonical reading
	// ({"alg","key_id","sha256","sig"}). Copied unchanged into the ledger document.
	GatewaySignature map[string]any `json:"gateway_signature,omitempty"`
}

// Submission is what goes on the ledger for one reading: WriteSensor's four arguments.
type Submission struct {
	ReadingID   string `json:"reading_id"`
	SensorID    string `json:"sensor_id"`
	Residues    string `json:"residues"`
	ReadingJSON string `json:"reading_json"`
}

func (s Submission) Args() []string {
	return []string{s.SensorID, s.ReadingID, s.Residues, s.ReadingJSON}
}

// quantity: gateway name -> chaincode field and the protocol's resolution.
// Units are the same on both sides (%VWC, degC, %RH, pH, lx); only names and rounding change.
var quantities = []struct {
	gateway, field string
	step           float64
}{
	{"soil_moisture", "soil_moisture", 0.1},
	{"temperature", "temp_c", 0.1},
	{"humidity", "humidity_pct", 0.1},
	{"ph", "ph", 0.01},
	{"illuminance", "light_lux", 5},
}

// ReadingID is R<node>-<seq>-<UTC date of capture>. The date suffix keeps IDs unique
// after the node's 16-bit sequence number wraps (65,536 readings); within one day a
// node cannot reuse a sequence number. It is derived only from the reading itself,
// so every retry and every restart produces the same ID.
func ReadingID(r GatewayReading) string {
	return fmt.Sprintf("R%03d-%04d-%s", r.NodeID, r.Seq, time.UnixMilli(r.ReceivedAtMs).UTC().Format("20060102"))
}

// ToSubmission converts a completed gateway reading into WriteSensor arguments.
// Quantities that were not reconstructed are sent as null, never as 0, with their
// gateway status under "quality". A reading with no quantity at all is refused.
func ToSubmission(r GatewayReading) (Submission, error) {
	if r.SensorID == "" || r.NodeID <= 0 || r.Seq < 0 || r.Seq > 65535 || r.ReceivedAtMs <= 0 {
		return Submission{}, fmt.Errorf("incomplete reading: sensor %q node %d seq %d time %d", r.SensorID, r.NodeID, r.Seq, r.ReceivedAtMs)
	}
	doc := map[string]any{
		"captured_at": time.UnixMilli(r.ReceivedAtMs).UTC().Format("2006-01-02T15:04:05.000Z"),
		"gateway_id":  r.GatewayID,
		"device_id":   r.DeviceID,
		"node_seq":    r.Seq,
		"event":       r.Event,
		"retries":     r.Retries,
	}
	quality := map[string]string{}
	res := make([]string, 0, len(quantities))
	present := 0
	for _, q := range quantities {
		st := r.Status[q.gateway]
		v := r.Values[q.gateway]
		if st == "reconstructed" && v != nil && !math.IsNaN(*v) {
			doc[q.field] = math.Round(*v/q.step) * q.step
			if q.step < 1 {
				doc[q.field], _ = strconv.ParseFloat(strconv.FormatFloat(doc[q.field].(float64), 'f', decimals(q.step), 64), 64)
			}
			present++
		} else {
			doc[q.field] = nil
			if st == "" {
				st = "missing"
			}
		}
		quality[q.field] = st
		res = append(res, strconv.Itoa(r.ResiduesReceived[q.gateway]))
	}
	if present == 0 {
		return Submission{}, fmt.Errorf("no quantity reconstructed for %s seq %d", r.SensorID, r.Seq)
	}
	doc["quality"] = quality
	if r.GatewaySignature != nil {
		doc["gateway_signature"] = r.GatewaySignature
	}
	b, err := json.Marshal(doc) // map keys are sorted: identical bytes on every retry
	if err != nil {
		return Submission{}, err
	}
	return Submission{ReadingID: ReadingID(r), SensorID: r.SensorID, Residues: strings.Join(res, ","), ReadingJSON: string(b)}, nil
}

func decimals(step float64) int {
	return int(math.Max(0, math.Ceil(-math.Log10(step)-1e-9)))
}
