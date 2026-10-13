package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

type querier interface {
	Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error)
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
}

type Mode int

const (
	// ModeWatermark: copy rows whose watermark column (e.g. updated_at) is
	// >= the target's current max. Picks up inserts and, for update
	// timestamps, updates.
	ModeWatermark Mode = iota
	// ModeIncrementalPK: append-only copy of rows with integer PK > target max.
	ModeIncrementalPK
	// ModeFullUpsert: no usable watermark but a PK exists; read every row and
	// upsert only the ones that are new or changed.
	ModeFullUpsert
	// ModeFullAppend: no watermark and no PK; read every row and insert only
	// those not already present in the target.
	ModeFullAppend
)

func (m Mode) String() string {
	return [...]string{"watermark", "incremental-pk", "full-upsert", "full-append"}[m]
}

type Plan struct {
	Mode      Mode
	Column    string // watermark column, or the PK column for ModeIncrementalPK
	Type      string // SQL type of Column
	Columns   []string
	PK        []string
	Watermark *string // current max on target; nil means target empty
}

type Result struct {
	Staged   int64 // rows read from source
	Affected int64 // rows inserted or updated on target
	Deleted  int64 // ModeWatermark without PK: rows re-loaded at the boundary
}

var intTypes = map[string]bool{"smallint": true, "integer": true, "bigint": true}

// plan decides how to sync a table. src and tgt must both be described.
func plan(cfg *Config, src, tgt *Table) (*Plan, error) {
	p := &Plan{}
	for _, c := range src.Columns {
		if _, ok := tgt.Column(c.Name); ok {
			p.Columns = append(p.Columns, c.Name)
		}
	}
	if len(p.Columns) == 0 {
		return nil, errors.New("no common columns between source and target")
	}
	common := func(n string) bool { _, ok := tgt.Column(n); _, ok2 := src.Column(n); return ok && ok2 }

	if len(tgt.PK) > 0 {
		p.PK = tgt.PK
		for _, k := range tgt.PK {
			if !common(k) {
				p.PK = nil
				break
			}
		}
	}

	if col, ok := cfg.TableWatermarks[src.FullName()]; ok {
		c, found := src.Column(col)
		if !found || !common(col) {
			return nil, watermarkColumnError(src, tgt, col)
		}
		p.Mode, p.Column, p.Type = ModeWatermark, c.Name, c.Type
		return p, nil
	}
	for _, name := range cfg.WatermarkColumns {
		if c, ok := src.Column(name); ok && common(name) {
			p.Mode, p.Column, p.Type = ModeWatermark, c.Name, c.Type
			return p, nil
		}
	}
	if len(p.PK) == 1 {
		c, _ := src.Column(p.PK[0])
		if intTypes[c.Type] {
			p.Mode, p.Column, p.Type = ModeIncrementalPK, c.Name, c.Type
			return p, nil
		}
	}
	if len(p.PK) > 0 {
		p.Mode = ModeFullUpsert
	} else {
		p.Mode = ModeFullAppend
	}
	return p, nil
}

// watermarkColumnError explains a bad SYNC_TABLE_WATERMARKS entry and lists
// columns that would work.
func watermarkColumnError(src, tgt *Table, col string) error {
	var missing []string
	if _, ok := src.Column(col); !ok {
		missing = append(missing, "source")
	}
	if _, ok := tgt.Column(col); !ok {
		missing = append(missing, "target")
	}
	var suitable []string
	for _, c := range src.Columns {
		if _, ok := tgt.Column(c.Name); ok && watermarkType(c.Type) {
			suitable = append(suitable, fmt.Sprintf("%s (%s)", c.Name, c.Type))
		}
	}
	return fmt.Errorf("SYNC_TABLE_WATERMARKS: column %q for %s is missing in %s table; suitable columns: [%s]",
		col, src.FullName(), strings.Join(missing, " and "), strings.Join(suitable, ", "))
}

// watermarkType reports whether values of this type increase over time in
// a way that suits a watermark.
func watermarkType(t string) bool {
	return intTypes[t] || t == "date" || t == "numeric" ||
		strings.HasPrefix(t, "timestamp") || strings.HasPrefix(t, "numeric(")
}

// loadWatermark reads the current max of the plan column from the target.
func loadWatermark(ctx context.Context, q querier, tgt *Table, p *Plan) error {
	if p.Mode != ModeWatermark && p.Mode != ModeIncrementalPK {
		return nil
	}
	return q.QueryRow(ctx, fmt.Sprintf("SELECT max(%s)::text FROM %s", quoteIdent(p.Column), tgt.Ident())).Scan(&p.Watermark)
}

func (p *Plan) sourceQuery(src *Table) string {
	q := fmt.Sprintf("SELECT %s FROM %s", identList(p.Columns), src.Ident())
	if p.Watermark != nil {
		op := ">="
		if p.Mode == ModeIncrementalPK {
			op = ">"
		}
		q += fmt.Sprintf(" WHERE %s %s %s::%s", quoteIdent(p.Column), op, quoteLiteral(*p.Watermark), p.Type)
	}
	return q
}

// syncTable copies the delta for one table inside a single target
// transaction: stream source rows via COPY into a temp staging table, then
// merge into the real table.
// With clean set, the target table is dropped and recreated first in the
// same transaction, so readers keep seeing the old data until commit and a
// failure leaves it untouched.
func syncTable(ctx context.Context, srcConn, tgtConn *pgx.Conn, src, tgt *Table, p *Plan, clean bool) (Result, error) {
	var res Result
	tx, err := tgtConn.Begin(ctx)
	if err != nil {
		return res, err
	}
	defer tx.Rollback(ctx)

	if clean {
		if _, err := tx.Exec(ctx, "DROP TABLE IF EXISTS "+tgt.Ident()); err != nil {
			return res, fmt.Errorf("drop: %w", err)
		}
		if err := ensureTargetTable(ctx, tx, src); err != nil {
			return res, fmt.Errorf("recreate: %w", err)
		}
	}

	// Re-read the watermark inside the transaction and lock the table so
	// concurrent runs cannot interleave.
	if _, err := tx.Exec(ctx, "LOCK TABLE "+tgt.Ident()+" IN SHARE ROW EXCLUSIVE MODE"); err != nil {
		return res, err
	}
	if err := loadWatermark(ctx, tx, tgt, p); err != nil {
		return res, fmt.Errorf("read watermark: %w", err)
	}

	const stage = "dbsync_stage"
	if _, err := tx.Exec(ctx, fmt.Sprintf(
		"CREATE TEMP TABLE %s ON COMMIT DROP AS SELECT %s FROM %s WITH NO DATA",
		stage, identList(p.Columns), tgt.Ident())); err != nil {
		return res, fmt.Errorf("create stage: %w", err)
	}

	res.Staged, err = streamCopy(ctx, srcConn, tx.Conn(),
		fmt.Sprintf("COPY (%s) TO STDOUT", p.sourceQuery(src)),
		fmt.Sprintf("COPY %s (%s) FROM STDIN", stage, identList(p.Columns)))
	if err != nil {
		return res, fmt.Errorf("copy: %w", err)
	}
	if res.Staged == 0 {
		return res, tx.Commit(ctx)
	}

	cols := identList(p.Columns)
	var merge string
	switch {
	case len(p.PK) > 0:
		merge = upsertSQL(tgt.Ident(), stage, p)
	case p.Mode == ModeWatermark:
		// No PK to dedupe on: drop the boundary rows on target and re-insert
		// everything from the source at or above the watermark.
		if p.Watermark != nil {
			tag, err := tx.Exec(ctx, fmt.Sprintf("DELETE FROM %s WHERE %s >= %s::%s",
				tgt.Ident(), quoteIdent(p.Column), quoteLiteral(*p.Watermark), p.Type))
			if err != nil {
				return res, fmt.Errorf("delete boundary rows: %w", err)
			}
			res.Deleted = tag.RowsAffected()
		}
		merge = fmt.Sprintf("INSERT INTO %s (%s) SELECT %s FROM %s", tgt.Ident(), cols, cols, stage)
	default: // ModeFullAppend
		merge = fmt.Sprintf("INSERT INTO %s (%s) SELECT %s FROM %s s WHERE NOT EXISTS (SELECT 1 FROM %s t WHERE %s)",
			tgt.Ident(), cols, cols, stage, tgt.Ident(), rowMatch("t", "s", p.Columns))
	}
	tag, err := tx.Exec(ctx, merge)
	if err != nil {
		return res, fmt.Errorf("merge: %w", err)
	}
	res.Affected = tag.RowsAffected()
	return res, tx.Commit(ctx)
}

// upsertSQL inserts staged rows and updates existing ones only when some
// column actually differs, so unchanged rows are not rewritten.
func upsertSQL(target, stage string, p *Plan) string {
	cols := identList(p.Columns)
	isPK := map[string]bool{}
	for _, k := range p.PK {
		isPK[k] = true
	}
	var sets, diffs []string
	for _, c := range p.Columns {
		if isPK[c] {
			continue
		}
		q := quoteIdent(c)
		sets = append(sets, fmt.Sprintf("%s = EXCLUDED.%s", q, q))
		// Compare as text so types without an equality operator (json, point, ...) work.
		diffs = append(diffs, fmt.Sprintf("t.%s::text IS DISTINCT FROM EXCLUDED.%s::text", q, q))
	}
	sql := fmt.Sprintf("INSERT INTO %s AS t (%s) SELECT %s FROM %s ON CONFLICT (%s) ",
		target, cols, cols, stage, identList(p.PK))
	if len(sets) == 0 {
		return sql + "DO NOTHING"
	}
	return sql + fmt.Sprintf("DO UPDATE SET %s WHERE %s", strings.Join(sets, ", "), strings.Join(diffs, " OR "))
}

func rowMatch(a, b string, cols []string) string {
	parts := make([]string, len(cols))
	for i, c := range cols {
		q := quoteIdent(c)
		parts[i] = fmt.Sprintf("%s.%s::text IS NOT DISTINCT FROM %s.%s::text", a, q, b, q)
	}
	return strings.Join(parts, " AND ")
}

// streamCopy pipes COPY TO STDOUT on src into COPY FROM STDIN on dst using
// the text format, which is type-agnostic as long as both sessions use the
// same datestyle etc. (set in dbConfig).
func streamCopy(ctx context.Context, src, dst *pgx.Conn, copyOut, copyIn string) (int64, error) {
	pr, pw := io.Pipe()
	outErr := make(chan error, 1)
	go func() {
		_, err := src.PgConn().CopyTo(ctx, pw, copyOut)
		pw.CloseWithError(err)
		outErr <- err
	}()
	tag, inErr := dst.PgConn().CopyFrom(ctx, pr, copyIn)
	pr.CloseWithError(errors.New("copy target aborted")) // unblock the writer if CopyFrom failed early
	if err := <-outErr; err != nil {
		return 0, fmt.Errorf("source: %w", err)
	}
	if inErr != nil {
		return 0, fmt.Errorf("target: %w", inErr)
	}
	return tag.RowsAffected(), nil
}
