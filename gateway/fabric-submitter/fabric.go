package main

import (
	"context"
	"errors"

	"github.com/hyperledger/fabric-gateway/pkg/client"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// fabricLedger implements Ledger with the Fabric Gateway SDK.
type fabricLedger struct {
	gw       *client.Gateway
	contract *client.Contract
}

func (f *fabricLedger) Endorse(ctx context.Context, args []string) (string, []byte, error) {
	prop, err := f.contract.NewProposal("WriteSensor", client.WithArguments(args...))
	if err != nil {
		return "", nil, err
	}
	tx, err := prop.EndorseWithContext(ctx)
	if err != nil {
		return "", nil, classify(err)
	}
	b, err := tx.Bytes()
	return tx.TransactionID(), b, err
}

func (f *fabricLedger) Submit(ctx context.Context, txBytes []byte) ([]byte, error) {
	tx, err := f.gw.NewTransaction(txBytes) // the same signed transaction, same transaction ID
	if err != nil {
		return nil, ErrPermanent{Reason: "stored transaction unreadable: " + err.Error()}
	}
	commit, err := tx.SubmitWithContext(ctx)
	if err != nil {
		return nil, classify(err)
	}
	return commit.Bytes()
}

func (f *fabricLedger) Status(ctx context.Context, commitBytes []byte) (string, uint64, error) {
	commit, err := f.gw.NewCommit(commitBytes)
	if err != nil {
		return "", 0, ErrPermanent{Reason: "stored commit request unreadable: " + err.Error()}
	}
	st, err := commit.StatusWithContext(ctx)
	if err != nil {
		return "", 0, classify(err)
	}
	return st.Code.String(), st.BlockNumber, nil
}

// classify turns Gateway errors into permanent failures (the chaincode refused the
// reading) or outages (everything else: peers or orderers unreachable, timeouts).
func classify(err error) error {
	var endorse *client.EndorseError
	if errors.As(err, &endorse) {
		msg := endorse.Error()
		for _, d := range status.Convert(err).Details() {
			if e, ok := d.(interface{ GetMessage() string }); ok {
				msg += "; " + e.GetMessage()
			}
		}
		if perm := classifyChaincodeError(msg); perm != nil {
			return perm
		}
	}
	if s, ok := status.FromError(err); ok && s.Code() == codes.InvalidArgument {
		return ErrPermanent{Reason: s.Message()}
	}
	return err
}
