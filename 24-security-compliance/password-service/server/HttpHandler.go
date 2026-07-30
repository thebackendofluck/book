// Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

package server

import (
	"encoding/json"
	"gopkg.in/DataDog/dd-trace-go.v1/ddtrace/tracer"
	"io/ioutil"
	"net/http"
)

func HashPassword(w http.ResponseWriter, r *http.Request) {
	span := tracer.StartSpan("post.hashPassword")
	defer span.Finish()
	child := tracer.StartSpan("process.request", tracer.ChildOf(span.Context()))
	var req HashRequest

	r.Body = http.MaxBytesReader(w, r.Body, 1<<20) // cap body at 1 MiB
	body, err := ioutil.ReadAll(r.Body)
	if err == nil {
		err = json.Unmarshal(body, &req)
	}
	if err != nil {
		child.Finish(tracer.WithError(err))
		w.WriteHeader(http.StatusBadRequest)
		w.Write(badRequestResponse())
		return
	}

	// Argon2id (OWASP-recommended, GPU-resistant), PHC-encoded with an
	// embedded per-hash salt. Replaces the legacy PBKDF2 Hash().
	hash, hErr := HashArgon2id(req.Password)
	if hErr != nil {
		child.Finish(tracer.WithError(hErr))
		w.WriteHeader(http.StatusInternalServerError)
		w.Write(badRequestResponse())
		return
	}
	resp, _ := json.Marshal(
		HashResponse{
			Success:  true,
			Hash:     hash,
			Revision: req.Revision,
		})
	child.Finish(tracer.WithError(nil))
	w.WriteHeader(http.StatusOK)
	w.Write(resp)
}

func HealthCheck(w http.ResponseWriter, r *http.Request) {
	resp, _ := json.Marshal(HealthResponse{Status: "ok"})

	w.WriteHeader(http.StatusOK)
	w.Write(resp)
}

func badRequestResponse() []byte {
	resp, _ := json.Marshal(
		&HashResponse{
			Success:      false,
			ErrorMessage: "Malformed request body",
		})

	return resp
}
