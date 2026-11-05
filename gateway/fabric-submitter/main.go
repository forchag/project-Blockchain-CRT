// fabric-submitter: takes completed readings from the gateway MAC service
// (readings.jsonl), submits each as one WriteSensor transaction, confirms the
// commit, and records reading ID, transaction ID and commit result together.
//
//	fabric-submitter -readings readings.jsonl -state state/ex-gw-north \
//	    -peer localhost:7051 -tls-ca peer-tls-ca.pem -cert ex-gw-north.pem -key ex-gw-north.key -msp NorthMSP
//
// Readings are journalled (fsync) before any network call, so nothing is lost
// during an internet or Fabric outage; the service retries with backoff and
// resumes after a restart. One signed transaction is reused per reading until it
// has a final commit status, so retries cannot create a second ledger record.
package main

import (
	"context"
	"crypto/x509"
	"errors"
	"flag"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/hyperledger/fabric-gateway/pkg/client"
	"github.com/hyperledger/fabric-gateway/pkg/hash"
	"github.com/hyperledger/fabric-gateway/pkg/identity"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
)

func main() {
	readings := flag.String("readings", "readings.jsonl", "gateway MAC service output (JSON lines)")
	stateDir := flag.String("state", "submitter-state", "journal and receipts directory (keep on persistent storage)")
	peer := flag.String("peer", "localhost:7051", "Fabric Gateway peer endpoint")
	override := flag.String("tls-host", "", "TLS server name override")
	tlsCA := flag.String("tls-ca", "", "peer TLS CA certificate (PEM)")
	certPath := flag.String("cert", "", "submitting identity's certificate (PEM)")
	keyPath := flag.String("key", "", "submitting identity's private key (PEM); never committed")
	msp := flag.String("msp", "NorthMSP", "MSP ID of the submitting identity")
	channel := flag.String("channel", "benchchannel", "channel")
	chaincode := flag.String("chaincode", "hrbac-cc", "chaincode")
	timeout := flag.Duration("timeout", 30*time.Second, "per-call timeout (endorse, submit, commit status)")
	poll := flag.Duration("poll", 2*time.Second, "how often to look for new readings")
	maxBackoff := flag.Duration("max-backoff", 5*time.Minute, "longest wait between retries during an outage")
	once := flag.Bool("once", false, "exit when every queued reading is final or an outage is hit")
	flag.Parse()
	logf := log.New(os.Stderr, "submitter ", log.LstdFlags|log.LUTC).Printf

	certPEM, err := os.ReadFile(*certPath)
	must(err)
	cert, err := identity.CertificateFromPEM(certPEM)
	must(err)
	policy, err := PolicyFromCert(cert)
	must(err)
	logf("identity %s role %s zone %s, delegated for %d sensors", policy.EnrollmentID, policy.Role, policy.Zone, len(policy.Sensors))

	ledger, closeFn, err := connect(cert, *keyPath, *msp, *peer, *tlsCA, *override, *channel, *chaincode, *timeout)
	must(err)
	defer closeFn()

	j, err := OpenJournal(*stateDir, time.Now)
	must(err)
	defer j.Close()
	r := &Runner{J: j, L: ledger, Policy: policy, Timeout: *timeout, Log: logf, Readings: *readings}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	backoff := time.Second
	for ctx.Err() == nil {
		n, err := r.Ingest()
		must(err)
		if n > 0 {
			logf("queued %d new readings; %d pending", n, len(j.Pending()))
		}
		done, err := r.Drain(ctx)
		if done > 0 {
			logf("%d readings final; %d pending", done, len(j.Pending()))
		}
		wait := *poll
		if err != nil {
			var outage ErrOutage
			if !errors.As(err, &outage) {
				must(err) // journal I/O failure: stop rather than lose track of a reading
			}
			logf("outage, retrying in %s: %v", backoff, err)
			wait, backoff = backoff, min(backoff*2, *maxBackoff)
		} else {
			backoff = time.Second
		}
		if *once && (err != nil || len(j.Pending()) == 0) {
			break
		}
		select {
		case <-ctx.Done():
		case <-time.After(wait):
		}
	}
	if len(j.Pending()) > 0 {
		os.Exit(3) // readings still waiting: the journal keeps them for the next run
	}
}

func connect(cert *x509.Certificate, keyPath, msp, peer, tlsCA, override, channel, chaincode string, timeout time.Duration) (Ledger, func(), error) {
	pemBytes, err := os.ReadFile(tlsCA)
	if err != nil {
		return nil, nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pemBytes) {
		return nil, nil, errors.New("no certificates in -tls-ca")
	}
	conn, err := grpc.NewClient(peer, grpc.WithTransportCredentials(credentials.NewClientTLSFromCert(pool, override)))
	if err != nil {
		return nil, nil, err
	}
	id, err := identity.NewX509Identity(msp, cert)
	if err != nil {
		return nil, nil, err
	}
	keyPEM, err := os.ReadFile(keyPath)
	if err != nil {
		return nil, nil, err
	}
	pk, err := identity.PrivateKeyFromPEM(keyPEM)
	if err != nil {
		return nil, nil, err
	}
	sign, err := identity.NewPrivateKeySign(pk)
	if err != nil {
		return nil, nil, err
	}
	gw, err := client.Connect(id, client.WithSign(sign), client.WithHash(hash.SHA256), client.WithClientConnection(conn),
		client.WithEndorseTimeout(timeout), client.WithSubmitTimeout(timeout), client.WithCommitStatusTimeout(timeout))
	if err != nil {
		return nil, nil, err
	}
	l := &fabricLedger{gw: gw, contract: gw.GetNetwork(channel).GetContract(chaincode)}
	return l, func() { gw.Close(); conn.Close() }, nil
}

func must(err error) {
	if err != nil {
		log.Fatal(err)
	}
}
