// Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

package main

import (
	"encoding/json"
	"fmt"
	"github.com/acmetocasino/go-password-service/server"
	"github.com/gorilla/mux"
	"gopkg.in/DataDog/dd-trace-go.v1/ddtrace/tracer"
	"io/ioutil"
	"log"
	"net/http"
	"time"
	"os"
)

func main() {
	readConfig()

	f, err := os.OpenFile("go-password-service.log", os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0644)
	if err != nil {
		log.Fatal(err)
	}

	defer f.Close()
	log.SetFlags(log.LstdFlags | log.Lshortfile)
	log.SetOutput(f)
	log.Println("Starting the application...")

	tracer.Start(
		tracer.WithEnv(server.Configuration.Env),
		tracer.WithService("go-password-service"),
		tracer.WithServiceVersion("1.0"),
	)
	defer tracer.Stop()

	httpAddress := fmt.Sprintf(
		server.Configuration.ServerIP+":%s",
		server.Configuration.RestServerPort)

	log.Println("HTTP listening at: " + httpAddress)

	httpServer(httpAddress)
}

func httpServer(httpAddress string) {
	router := mux.NewRouter().StrictSlash(true)
	router.Methods("POST").Path("/hashPassword").HandlerFunc(server.HashPassword)
	router.Methods("GET").Path("/health").HandlerFunc(server.HealthCheck)

	srv := &http.Server{
		Addr:              httpAddress,
		Handler:           router,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	log.Fatal(srv.ListenAndServe())
}

func readConfig() error {
	all, err := ioutil.ReadFile("./resources/config.json")
	if err != nil {
		log.Fatal("Error reading resources")
		return err
	}

	err = json.Unmarshal(all, &server.Configuration)
	if err != nil {
		log.Fatal("Error unmarshalling config file")
		return err
	}
	return nil
}
