package main

import (
	"flag"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/CLSMCSMII/browserstream/core"
)

func main() {
	defaultPath := os.Getenv("BROWSERSTREAM_CONFIG")
	if defaultPath == "" {
		defaultPath = "config.json"
	}
	configPath := flag.String("config", defaultPath, "path to JSON configuration")
	validate := flag.Bool("validate-config", false, "validate configuration and exit")
	flag.Parse()
	cfg, err := core.LoadConfig(*configPath)
	if err != nil {
		log.Fatalf("configuration error: %v", err)
	}
	if *validate {
		log.Printf("configuration valid: %d room(s)", len(cfg.Rooms))
		return
	}
	srv := &http.Server{Addr: cfg.ListenAddress, Handler: core.NewApp(cfg).Handler(), ReadHeaderTimeout: time.Duration(cfg.Limits.ReadTimeoutSeconds) * time.Second, ReadTimeout: time.Duration(cfg.Limits.ReadTimeoutSeconds) * time.Second, WriteTimeout: time.Duration(cfg.Limits.WriteTimeoutSeconds) * time.Second, IdleTimeout: time.Duration(cfg.Limits.IdleTimeoutSeconds) * time.Second, MaxHeaderBytes: 1 << 20}
	log.Printf("%s listening on %s", cfg.AppName, cfg.ListenAddress)
	if cfg.TLS.CertFile != "" {
		err = srv.ListenAndServeTLS(cfg.TLS.CertFile, cfg.TLS.KeyFile)
	} else {
		err = srv.ListenAndServe()
	}
	if err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}
