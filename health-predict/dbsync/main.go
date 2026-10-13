// Command dbsync copies new and changed rows from every table in a source
// Postgres database into a target Postgres database.
package main

import (
	"bufio"
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
	"golang.org/x/term"
)

func main() {
	envFile := flag.String("env", "", "path to .env file (default: ./.env, then ../.env)")
	dryRun := flag.Bool("dry-run", false, "show the plan for each table without writing anything")
	check := flag.Bool("check", false, "check source and target connections and privileges, then exit")
	clean := flag.Bool("clean", false, "drop each synced table on the target and reload it in full")
	yes := flag.Bool("yes", false, "with -clean: skip the confirmation prompt")
	flag.Parse()

	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, nil)))
	opts := Options{DryRun: *dryRun, Clean: *clean, Yes: *yes}
	if err := run(*envFile, *check, opts); err != nil {
		slog.Error("failed", "err", err)
		os.Exit(1)
	}
}

type Options struct {
	DryRun bool
	Clean  bool // drop and fully reload each synced target table
	Yes    bool // skip the -clean confirmation
}

func run(envFile string, check bool, opts Options) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := loadEnv(envFile); err != nil {
		return err
	}
	cfg, err := loadConfig()
	if err != nil {
		return err
	}

	if check {
		return checkConnections(ctx, cfg)
	}

	srcConn, err := connect(ctx, "source", cfg.Source)
	if err != nil {
		return err
	}
	defer srcConn.Close(context.Background())
	tgtConn, err := connect(ctx, "target", cfg.Target)
	if err != nil {
		return err
	}
	defer tgtConn.Close(context.Background())

	// Read-only on the source, always.
	if _, err := srcConn.Exec(ctx, "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"); err != nil {
		return err
	}

	tables, err := listTables(ctx, srcConn, cfg.Schemas)
	if err != nil {
		return err
	}
	slog.Info("discovered source tables", "count", len(tables), "schemas", cfg.Schemas)

	var selected []*Table
	for _, t := range tables {
		if cfg.Exclude[t.FullName()] {
			slog.Info("skipped (excluded)", "table", t.FullName())
			continue
		}
		selected = append(selected, t)
	}
	tables = selected

	if opts.Clean && !opts.DryRun {
		if err := confirmClean(ctx, tgtConn, tables, opts.Yes); err != nil {
			return err
		}
	}

	types := newTypeSyncer(srcConn, tgtConn, opts.DryRun)
	start := time.Now()
	var failed int
	var total Result
	for _, src := range tables {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		log := slog.With("table", src.FullName())
		res, err := syncOne(ctx, cfg, srcConn, tgtConn, types, src, opts, log)
		if err != nil {
			failed++
			log.Error("table failed", "err", err)
			continue
		}
		total.Staged += res.Staged
		total.Affected += res.Affected
	}

	slog.Info("done", "tables", len(tables), "failed", failed,
		"rows_read", total.Staged, "rows_written", total.Affected, "elapsed", time.Since(start).Round(time.Millisecond))
	if failed > 0 {
		return errors.New("one or more tables failed")
	}
	return nil
}

func syncOne(ctx context.Context, cfg *Config, srcConn, tgtConn *pgx.Conn, types *typeSyncer, src *Table, opts Options, log *slog.Logger) (Result, error) {
	dryRun := opts.DryRun
	// Runs every time, not only on table creation, so enum values added on
	// the source later are added to the target before data arrives.
	if err := types.ensureTable(ctx, src); err != nil {
		return Result{}, err
	}
	tgt := &Table{Schema: src.Schema, Name: src.Name}
	err := describeTable(ctx, tgtConn, tgt)
	exists := err == nil
	switch {
	case opts.Clean:
		// The table is dropped and recreated from the source definition
		// inside syncTable's transaction, so plan against the source shape.
		if err != nil && !errors.Is(err, errNoTable) {
			return Result{}, err
		}
		*tgt = *src
		exists = false
		if dryRun {
			log.Info("would drop and reload target table")
		}
	case errors.Is(err, errNoTable) && dryRun:
		*tgt = *src // plan as if it had been created
		log.Info("would create target table")
	case errors.Is(err, errNoTable):
		if err := ensureTargetTable(ctx, tgtConn, src); err != nil {
			return Result{}, err
		}
		log.Info("created target table")
		if err := describeTable(ctx, tgtConn, tgt); err != nil {
			return Result{}, err
		}
	case err != nil:
		return Result{}, err
	}

	p, err := plan(cfg, src, tgt)
	if err != nil {
		return Result{}, err
	}

	if dryRun {
		if exists {
			if err := loadWatermark(ctx, tgtConn, tgt, p); err != nil {
				return Result{}, err
			}
		}
		log.Info("plan", "mode", p.Mode, "column", p.Column, "watermark", deref(p.Watermark), "query", p.sourceQuery(src))
		return Result{}, nil
	}

	t0 := time.Now()
	res, err := syncTable(ctx, srcConn, tgtConn, src, tgt, p, opts.Clean)
	if err != nil {
		return res, err
	}
	mode := p.Mode.String()
	if opts.Clean {
		mode = "clean"
	}
	attrs := []any{"mode", mode, "rows_read", res.Staged, "rows_written", res.Affected, "elapsed", time.Since(t0).Round(time.Millisecond)}
	if p.Column != "" {
		attrs = append(attrs, "column", p.Column, "from", deref(p.Watermark))
	}
	if res.Deleted > 0 {
		attrs = append(attrs, "boundary_rows_replaced", res.Deleted)
	}
	log.Info("synced", attrs...)
	return res, nil
}

// confirmClean lists the target tables -clean will drop and asks for "yes".
func confirmClean(ctx context.Context, tgtConn *pgx.Conn, tables []*Table, yes bool) error {
	var existing []string
	for _, t := range tables {
		var ok bool
		if err := tgtConn.QueryRow(ctx, "SELECT to_regclass($1) IS NOT NULL", t.Ident()).Scan(&ok); err != nil {
			return err
		}
		if ok {
			existing = append(existing, t.FullName())
		}
	}
	slog.Warn("clean mode: these target tables will be dropped and fully reloaded", "count", len(existing), "tables", existing)
	if yes || len(existing) == 0 {
		return nil
	}
	if !term.IsTerminal(int(os.Stdin.Fd())) {
		return errors.New("-clean needs confirmation: run interactively or pass -yes")
	}
	fmt.Fprint(os.Stderr, "Type 'yes' to continue: ")
	answer, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	if strings.TrimSpace(answer) != "yes" {
		return errors.New("aborted: clean not confirmed")
	}
	return nil
}

func deref(s *string) string {
	if s == nil {
		return "<none>"
	}
	return *s
}

// safeErr returns the first line of a connection error with every
// connection detail from .env (and any IP address) masked out.
func safeErr(err error, cc *pgx.ConnConfig) string {
	msg, _, _ := strings.Cut(err.Error(), "\n")
	msg = ipPattern.ReplaceAllString(msg, "***")
	for _, v := range []string{cc.Password, cc.Host, cc.User, cc.Database, strconv.Itoa(int(cc.Port))} {
		if len(v) >= 2 { // avoid masking single characters across the whole message
			msg = strings.ReplaceAll(msg, v, "***")
		}
	}
	return msg
}

var ipPattern = regexp.MustCompile(`\b\d{1,3}(\.\d{1,3}){3}\b|\[?[0-9a-fA-F]*:[0-9a-fA-F:]+:[0-9a-fA-F]+\]?`)
