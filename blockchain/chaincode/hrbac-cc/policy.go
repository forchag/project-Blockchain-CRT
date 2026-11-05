package main

import (
	"encoding/json"
	"fmt"
	"math"
	"strings"
)

// decideWrite is the WriteSensor identity policy. It is a pure function of the
// caller's certificate attributes so it can be tested without a peer.
//
//	Sensor:  enrollID must equal sensorID.
//	Gateway: sensorID must be one of the "|"-separated IDs in the sensors attribute.
//	others (Admin included):  denied.
func decideWrite(role, enrollID, sensorsAttr, sensorID string) error {
	if sensorID == "" {
		return fmt.Errorf("malformed reading: empty sensorID")
	}
	switch role {
	case "Sensor":
		if enrollID != sensorID {
			return fmt.Errorf("HRBAC denied: ROLE_INSUFFICIENT (enrollment %s writing for %s)", enrollID, sensorID)
		}
		return nil
	case "Gateway":
		for _, s := range strings.Split(sensorsAttr, "|") {
			if strings.TrimSpace(s) == sensorID {
				return nil
			}
		}
		return fmt.Errorf("HRBAC denied: SENSOR_NOT_DELEGATED (gateway %s is not delegated for %s)", enrollID, sensorID)
	default: // includes Admin: its table entry never let it write another identity's reading, and still doesn't

		return fmt.Errorf("HRBAC denied: OP_NOT_PERMITTED (role=%s operation=WriteSensor)", role)
	}
}

// Admissible ranges, identical to the radio protocol's scaling limits
// A null value means the quantity was not delivered.
var readingRanges = map[string][2]float64{
	"soil_moisture": {0, 100},
	"temp_c":        {-40, 85},
	"humidity_pct":  {0, 100},
	"ph":            {0, 14},
	"light_lux":     {0, 48980},
}

// buildRecord validates readingJSON and returns the stored record: the reading's
// own fields plus sensor, reading ID, residues and who submitted it. A missing or
// null quantity stays null; it is never stored as 0. json.Marshal sorts map keys,
// so every endorser produces identical bytes.
func buildRecord(readingJSON, sensorID, readingID, residues, submitter, role string) ([]byte, error) {
	if readingID == "" {
		return nil, fmt.Errorf("malformed reading: empty readingID")
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(readingJSON), &m); err != nil {
		return nil, fmt.Errorf("malformed reading: %v", err)
	}
	if ts, ok := m["captured_at"].(string); !ok || ts == "" {
		return nil, fmt.Errorf("malformed reading: captured_at missing")
	}
	present := 0
	for k, lim := range readingRanges {
		v, ok := m[k]
		if !ok || v == nil {
			m[k] = nil
			continue
		}
		f, isNum := v.(float64)
		if !isNum || math.IsNaN(f) || f < lim[0] || f > lim[1] {
			return nil, fmt.Errorf("malformed reading: %s=%v outside [%g, %g]", k, v, lim[0], lim[1])
		}
		present++
	}
	if present == 0 {
		return nil, fmt.Errorf("malformed reading: no quantity present")
	}
	for _, k := range []string{"sensor_id", "reading_id", "submitter", "submitter_role"} {
		if _, clash := m[k]; clash {
			return nil, fmt.Errorf("malformed reading: field %s is set by the chaincode", k)
		}
	}
	m["sensor_id"], m["reading_id"], m["submitter"], m["submitter_role"] = sensorID, readingID, submitter, role
	if residues != "" {
		m["residues_received"] = residues
	}
	return json.Marshal(m)
}
