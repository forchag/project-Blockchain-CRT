// hrbac-cc: hierarchical role-based access control for the CRT sensor network (public reference chaincode).
//
// Roles, zones and enrollment IDs come from the creator's X.509 attributes
// (Fabric CA extension 1.2.3.4.5.6.7.8.1: role, zone, hf.EnrollmentID, sensors).
//
// Who may write a sensor's reading (policy.go):
//   - role Sensor:  only its own readings (hf.EnrollmentID == sensorID);
//   - role Gateway: only readings of sensors listed in its "sensors" attribute
//     ("|"-separated, fixed by the CA at enrollment); every other sensor is denied;
//   - any other role: denied.
//
// The stored record names the submitter and its role, so a gateway-attested
// reading is never mistaken for one signed by the sensor.
// Every call is checked against the role -> operation table below; a denial is
// a chaincode error (status 500 -> endorsement fails, nothing is ordered).
//
// HRBAC_ENFORCE=false disables the checks (benchmark baseline only).
// Runs as chaincode-as-a-service: CHAINCODE_SERVER_ADDRESS, CHAINCODE_ID.
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strconv"

	"github.com/hyperledger/fabric-chaincode-go/v2/pkg/cid"
	"github.com/hyperledger/fabric-chaincode-go/v2/shim"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

type Contract struct {
	contractapi.Contract
	enforce bool
}

// permissions[role][operation] = zone-scoped?
var permissions = map[string]map[string]bool{
	"Sensor":      {"WriteSensor": true},
	"Gateway":     {"WriteSensor": true},
	"Farmer":      {"ReadSensorData": true, "ReadZone": true, "ControlZone": true, "ReadEnvironmentalRecord": true},
	"Agronomist":  {"ReadSensorData": false, "ReadEnvironmentalRecord": false, "ReadZone": false, "ControlZone": true, "ReadProvenance": false},
	"Certifier":   {"ReadProvenance": false, "ReadAudit": false, "ReadSensorData": false, "ReadEnvironmentalRecord": false},
	"SupplyChain": {"ReadProvenance": false},
	"Admin": {"ReadAudit": false, "ReadZone": false, "ControlZone": false, "ReadProvenance": false,
		"ReadSensorData": false, "ReadEnvironmentalRecord": false, "WriteSensor": false},
}

func (c *Contract) authorize(ctx contractapi.TransactionContextInterface, op, zone string) (string, error) {
	id, _ := cid.GetID(ctx.GetStub())
	if !c.enforce {
		return id, nil
	}
	role, ok, err := cid.GetAttributeValue(ctx.GetStub(), "role")
	if err != nil || !ok {
		return "", fmt.Errorf("HRBAC denied: ROLE_INSUFFICIENT (no role attribute)")
	}
	scoped, allowed := permissions[role][op]
	if !allowed {
		return "", fmt.Errorf("HRBAC denied: OP_NOT_PERMITTED (role=%s operation=%s)", role, op)
	}
	if scoped && zone != "" {
		myZone, _, _ := cid.GetAttributeValue(ctx.GetStub(), "zone")
		if myZone != zone {
			return "", fmt.Errorf("HRBAC denied: ZONE_MISMATCH (caller zone=%s target zone=%s)", myZone, zone)
		}
	}
	return id, nil
}

// WriteSensor(sensorID, readingID, residues, readingJSON) stores one reading under
// reading~<sensor>~<reading>. The key is never read first (blind write: no MVCC
// conflicts); the key is unique per reading, so a resubmission cannot create a
// second record. The submitting client reuses one signed transaction per reading,
// so the ledger normally holds a single VALID transaction for it as well.
func (c *Contract) WriteSensor(ctx contractapi.TransactionContextInterface, sensorID, readingID, residues, readingJSON string) (string, error) {
	submitter, _ := cid.GetID(ctx.GetStub())
	role := ""
	if c.enforce {
		var ok bool
		var err error
		role, ok, err = cid.GetAttributeValue(ctx.GetStub(), "role")
		if err != nil || !ok {
			return "", fmt.Errorf("HRBAC denied: ROLE_INSUFFICIENT (no role attribute)")
		}
		enroll, _, _ := cid.GetAttributeValue(ctx.GetStub(), "hf.EnrollmentID")
		sensors, _, _ := cid.GetAttributeValue(ctx.GetStub(), "sensors")
		if err := decideWrite(role, enroll, sensors, sensorID); err != nil {
			return "", err
		}
		if _, err := c.authorize(ctx, "WriteSensor", ""); err != nil {
			return "", err
		}
		submitter = enroll
	}
	rec, err := buildRecord(readingJSON, sensorID, readingID, residues, submitter, role)
	if err != nil {
		return "", err
	}
	key, err := ctx.GetStub().CreateCompositeKey("reading", []string{sensorID, readingID})
	if err != nil {
		return "", err
	}
	if err := ctx.GetStub().PutState(key, rec); err != nil {
		return "", err
	}
	return `{"decision":"allowed"}`, nil
}

// ControlZone(zone, settingsJSON) reads then writes zone~<zone>; concurrent writes to one zone conflict (MVCC).
func (c *Contract) ControlZone(ctx contractapi.TransactionContextInterface, zone, settingsJSON string) (string, error) {
	id, err := c.authorize(ctx, "ControlZone", zone)
	if err != nil {
		return "", err
	}
	key, _ := ctx.GetStub().CreateCompositeKey("zone", []string{zone})
	if _, err := ctx.GetStub().GetState(key); err != nil {
		return "", err
	}
	v, _ := json.Marshal(map[string]string{"controlled_by": id, "settings": settingsJSON})
	return `{"decision":"allowed"}`, ctx.GetStub().PutState(key, v)
}

// ReadSensorData(sensorID, limit) returns the newest readings of one sensor (evaluate only).
func (c *Contract) ReadSensorData(ctx contractapi.TransactionContextInterface, sensorID, zone, limit string) ([]string, error) {
	if _, err := c.authorize(ctx, "ReadSensorData", zone); err != nil {
		return nil, err
	}
	n, _ := strconv.Atoi(limit)
	if n <= 0 || n > 500 {
		n = 48
	}
	it, err := ctx.GetStub().GetStateByPartialCompositeKey("reading", []string{sensorID})
	if err != nil {
		return nil, err
	}
	defer it.Close()
	var out []string
	for it.HasNext() {
		kv, err := it.Next()
		if err != nil {
			return nil, err
		}
		out = append(out, string(kv.Value))
	}
	if len(out) > n {
		out = out[len(out)-n:]
	}
	return out, nil
}

func (c *Contract) ReadZone(ctx contractapi.TransactionContextInterface, zone string) (string, error) {
	if _, err := c.authorize(ctx, "ReadZone", zone); err != nil {
		return "", err
	}
	key, _ := ctx.GetStub().CreateCompositeKey("zone", []string{zone})
	b, err := ctx.GetStub().GetState(key)
	return string(b), err
}

func main() {
	if os.Getenv("CHAINCODE_ID") == "" || os.Getenv("CHAINCODE_SERVER_ADDRESS") == "" {
		log.Fatal("CHAINCODE_ID and CHAINCODE_SERVER_ADDRESS must be set (network.sh deploy sets CHAINCODE_ID)")
	}
	enforce := os.Getenv("HRBAC_ENFORCE") != "false"
	cc, err := contractapi.NewChaincode(&Contract{enforce: enforce})
	if err != nil {
		log.Fatal(err)
	}
	server := &shim.ChaincodeServer{
		CCID:    os.Getenv("CHAINCODE_ID"),
		Address: os.Getenv("CHAINCODE_SERVER_ADDRESS"),
		CC:      cc,
		TLSProps: shim.TLSProperties{
			Disabled: os.Getenv("CHAINCODE_TLS_DISABLED") == "true",
		},
	}
	log.Printf("hrbac-cc starting, HRBAC_ENFORCE=%v", enforce)
	if err := server.Start(); err != nil {
		log.Fatal(err)
	}
}
