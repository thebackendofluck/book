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

type HashRequest struct {
	Password string `json:"password"`
	Salt     string `json:"salt"`
	Revision int32  `json:"revision"`
}

type HashResponse struct {
	Success      bool   `json:"success"`
	ErrorMessage string `json:"errorMessage,omitempty"`
	Hash         string `json:"hash,omitempty"`
	Revision     int32  `json:"revision"`
}

type HealthResponse struct {
	Status string `json:"status"`
}
