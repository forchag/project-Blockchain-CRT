package main

import (
	"crypto/x509"
	"encoding/asn1"
	"encoding/json"
	"fmt"
	"strings"
)

// Fabric CA writes enrollment attributes into this X.509 extension as
// {"attrs":{"role":"Gateway","zone":"North","sensors":"ex-sns-001|...","hf.EnrollmentID":"ex-gw-north"}}.
var attrOID = asn1.ObjectIdentifier{1, 2, 3, 4, 5, 6, 7, 8, 1}

// Policy is the client-side copy of the chaincode's WriteSensor identity policy
// (blockchain/chaincode/hrbac-cc/policy.go). It lets the service refuse a reading it is not
// delegated for without spending an endorsement; the chaincode check stays authoritative.
type Policy struct {
	EnrollmentID string
	Role         string
	Zone         string
	Sensors      map[string]bool
}

func PolicyFromCert(cert *x509.Certificate) (Policy, error) {
	p := Policy{EnrollmentID: cert.Subject.CommonName, Sensors: map[string]bool{}}
	var attrs map[string]string
	for _, ext := range cert.Extensions {
		if ext.Id.Equal(attrOID) {
			var doc struct {
				Attrs map[string]string `json:"attrs"`
			}
			if err := json.Unmarshal(ext.Value, &doc); err != nil {
				return p, fmt.Errorf("attribute extension: %v", err)
			}
			attrs = doc.Attrs
		}
	}
	if attrs == nil {
		return p, fmt.Errorf("certificate %q has no Fabric CA attributes (role, sensors)", p.EnrollmentID)
	}
	p.Role, p.Zone = attrs["role"], attrs["zone"]
	if id := attrs["hf.EnrollmentID"]; id != "" {
		p.EnrollmentID = id
	}
	for _, s := range strings.Split(attrs["sensors"], "|") {
		if s = strings.TrimSpace(s); s != "" {
			p.Sensors[s] = true
		}
	}
	switch p.Role {
	case "Gateway":
		if len(p.Sensors) == 0 {
			return p, fmt.Errorf("gateway identity %q has an empty sensors attribute", p.EnrollmentID)
		}
	case "Sensor":
	default:
		return p, fmt.Errorf("identity %q has role %q; WriteSensor needs Gateway or Sensor", p.EnrollmentID, p.Role)
	}
	return p, nil
}

// Allows mirrors decideWrite in the chaincode.
func (p Policy) Allows(sensorID string) error {
	switch p.Role {
	case "Sensor":
		if sensorID != p.EnrollmentID {
			return fmt.Errorf("sensor identity %s cannot submit for %s", p.EnrollmentID, sensorID)
		}
	case "Gateway":
		if !p.Sensors[sensorID] {
			return fmt.Errorf("gateway %s is not delegated for %s", p.EnrollmentID, sensorID)
		}
	default:
		return fmt.Errorf("role %q cannot submit readings", p.Role)
	}
	return nil
}
