// loadgen: closed-loop Fabric Gateway load generator for benchmarks/workload.example.yaml.
//
// Payloads are synthetic: uniformly random values inside the ranges of workload.example.yaml.
// They are not sensor readings and carry no field data.
//
// N workers each keep exactly one WriteSensor transaction in flight: endorse,
// submit, wait for the commit status, repeat. Per-transaction timings go to
// <out>/transactions.csv; the trial summary to <out>/summary.json.
//
//	loadgen -peer peer0.north.bench:7051 -tls-ca ca.pem -cert user.pem -key user_sk \
//	        -msp NorthMSP -concurrency 40 -duration 60s -warmup 10s -out results/trial-001
package main

import (
	"context"
	"crypto/x509"
	"encoding/csv"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/hyperledger/fabric-gateway/pkg/client"
	"github.com/hyperledger/fabric-gateway/pkg/hash"
	"github.com/hyperledger/fabric-gateway/pkg/identity"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
)

type result struct {
	worker              int
	txID                string
	start               time.Time
	endorseMs, submitMs float64
	commitMs, totalMs   float64
	status              string
	err                 string
}

func must(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "loadgen:", err)
		os.Exit(2)
	}
}

func main() {
	peer := flag.String("peer", "localhost:7051", "gateway peer endpoint")
	override := flag.String("tls-host", "", "TLS server name override")
	tlsCA := flag.String("tls-ca", "", "peer TLS CA certificate (PEM)")
	certPath := flag.String("cert", "", "client certificate (PEM)")
	keyPath := flag.String("key", "", "client private key (PEM); never committed")
	msp := flag.String("msp", "NorthMSP", "client MSP ID")
	channel := flag.String("channel", "benchchannel", "channel")
	chaincode := flag.String("chaincode", "hrbac-cc", "chaincode")
	concurrency := flag.Int("concurrency", 10, "closed-loop workers")
	duration := flag.Duration("duration", 60*time.Second, "measured duration")
	warmup := flag.Duration("warmup", 10*time.Second, "unmeasured warm-up")
	timeout := flag.Duration("timeout", 30*time.Second, "per-transaction timeout (endorse+submit+commit)")
	out := flag.String("out", "results", "output directory")
	seed := flag.Int64("seed", 1, "payload RNG seed")
	flag.Parse()

	pemBytes, err := os.ReadFile(*tlsCA)
	must(err)
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pemBytes) {
		must(errors.New("no certificates in -tls-ca"))
	}
	conn, err := grpc.NewClient(*peer, grpc.WithTransportCredentials(credentials.NewClientTLSFromCert(pool, *override)))
	must(err)
	defer conn.Close()
	certPEM, err := os.ReadFile(*certPath)
	must(err)
	cert, err := identity.CertificateFromPEM(certPEM)
	must(err)
	id, err := identity.NewX509Identity(*msp, cert)
	must(err)
	keyPEM, err := os.ReadFile(*keyPath)
	must(err)
	pk, err := identity.PrivateKeyFromPEM(keyPEM)
	must(err)
	sign, err := identity.NewPrivateKeySign(pk)
	must(err)
	gw, err := client.Connect(id, client.WithSign(sign), client.WithHash(hash.SHA256), client.WithClientConnection(conn),
		client.WithEndorseTimeout(*timeout), client.WithSubmitTimeout(*timeout), client.WithCommitStatusTimeout(*timeout))
	must(err)
	defer gw.Close()
	contract := gw.GetNetwork(*channel).GetContract(*chaincode)
	// HRBAC requires the writer's enrollment ID (certificate CN) to equal the sensor ID.
	sensorID := cert.Subject.CommonName
	must(os.MkdirAll(*out, 0o755))

	var seq atomic.Int64
	results := make(chan result, 1<<16)
	ctx, cancel := context.WithCancel(context.Background())
	measureFrom := time.Now().Add(*warmup)
	stopAt := measureFrom.Add(*duration)
	var wg sync.WaitGroup
	for w := 0; w < *concurrency; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			rng := rand.New(rand.NewSource(*seed + int64(w)))
			for time.Now().Before(stopAt) && ctx.Err() == nil {
				n := seq.Add(1)
				reading := fmt.Sprintf(`{"captured_at":"%s","soil_moisture":%.1f,"temp_c":%.1f,"humidity_pct":%.1f,"ph":%.2f,"light_lux":%d}`,
					time.Now().UTC().Format(time.RFC3339), 20+60*rng.Float64(), 15+20*rng.Float64(), 35+50*rng.Float64(),
					6.1+1.1*rng.Float64(), rng.Intn(45000))
				r := result{worker: w, start: time.Now()}
				tctx, tcancel := context.WithTimeout(ctx, *timeout)
				prop, err := contract.NewProposal("WriteSensor", client.WithArguments(sensorID, fmt.Sprintf("B%09d", n), "", reading))
				if err == nil {
					r.txID = prop.TransactionID()
					var tx *client.Transaction
					tx, err = prop.EndorseWithContext(tctx)
					r.endorseMs = ms(time.Since(r.start))
					if err == nil {
						t1 := time.Now()
						var commit *client.Commit
						commit, err = tx.SubmitWithContext(tctx)
						r.submitMs = ms(time.Since(t1))
						if err == nil {
							t2 := time.Now()
							var st *client.Status
							st, err = commit.StatusWithContext(tctx)
							r.commitMs = ms(time.Since(t2))
							if err == nil {
								r.status = st.Code.String()
							}
						}
					}
				}
				tcancel()
				r.totalMs = ms(time.Since(r.start))
				if err != nil {
					r.status, r.err = classify(err), err.Error()
				}
				results <- r
			}
		}(w)
	}
	go func() { wg.Wait(); close(results) }()

	f, err := os.Create(filepath.Join(*out, "transactions.csv"))
	must(err)
	cw := csv.NewWriter(f)
	cw.Write([]string{"worker", "tx_id", "start_utc", "phase", "endorse_ms", "submit_ms", "commit_ms", "total_ms", "status", "error"})
	var lat []float64
	counts := map[string]int{}
	for r := range results {
		phase := "measured"
		if r.start.Before(measureFrom) {
			phase = "warmup"
		} else if r.start.After(stopAt) {
			phase = "drain"
		}
		cw.Write([]string{strconv.Itoa(r.worker), r.txID, r.start.UTC().Format(time.RFC3339Nano), phase, f3(r.endorseMs),
			f3(r.submitMs), f3(r.commitMs), f3(r.totalMs), r.status, r.err})
		if phase != "measured" {
			continue
		}
		counts["submitted"]++
		if r.submitMs > 0 || r.status == "VALID" { // endorsement succeeded iff the client went on to submit
			counts["endorsed"]++
		}
		if r.status == "VALID" {
			counts["committed"]++
			lat = append(lat, r.totalMs)
		} else {
			counts["failed"]++
			counts["status_"+r.status]++
		}
	}
	cancel()
	cw.Flush()
	f.Close()
	sort.Float64s(lat)
	summary := map[string]any{
		"concurrency": *concurrency, "duration_s": duration.Seconds(), "warmup_s": warmup.Seconds(),
		"counts": counts, "throughput_tps": float64(counts["committed"]) / duration.Seconds(),
		"latency_ms": map[string]float64{"mean": mean(lat), "p50": pct(lat, 50), "p95": pct(lat, 95), "p99": pct(lat, 99),
			"sd": sd(lat)},
		"started_utc": measureFrom.UTC().Format(time.RFC3339), "ended_utc": stopAt.UTC().Format(time.RFC3339),
	}
	b, _ := json.MarshalIndent(summary, "", "  ")
	must(os.WriteFile(filepath.Join(*out, "summary.json"), append(b, '\n'), 0o644))
	fmt.Println(string(b))
	if counts["committed"] == 0 {
		os.Exit(1)
	}
}

func classify(err error) string {
	var e *client.EndorseError
	var s *client.SubmitError
	var c *client.CommitStatusError
	var ce *client.CommitError
	switch {
	case errors.As(err, &e):
		return "ENDORSE_FAILED"
	case errors.As(err, &s):
		return "SUBMIT_FAILED"
	case errors.As(err, &c):
		return "COMMIT_STATUS_TIMEOUT"
	case errors.As(err, &ce):
		return "COMMIT_INVALID"
	case errors.Is(err, context.DeadlineExceeded):
		return "TIMEOUT"
	}
	return "ERROR"
}

func ms(d time.Duration) float64 { return float64(d.Microseconds()) / 1000 }
func f3(v float64) string        { return strconv.FormatFloat(v, 'f', 3, 64) }
func mean(x []float64) float64 {
	if len(x) == 0 {
		return math.NaN()
	}
	s := 0.0
	for _, v := range x {
		s += v
	}
	return s / float64(len(x))
}
func sd(x []float64) float64 {
	if len(x) < 2 {
		return math.NaN()
	}
	m, s := mean(x), 0.0
	for _, v := range x {
		s += (v - m) * (v - m)
	}
	return math.Sqrt(s / float64(len(x)-1))
}
func pct(x []float64, p float64) float64 {
	if len(x) == 0 {
		return math.NaN()
	}
	return x[int(math.Min(float64(len(x)-1), math.Ceil(p/100*float64(len(x)))-1))]
}
