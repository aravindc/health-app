package main

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5"
)

type Column struct {
	Name    string
	Type    string // format_type() output, e.g. "timestamp with time zone"
	NotNull bool
	TypeOID uint32
}

type Table struct {
	Schema  string
	Name    string
	Columns []Column
	PK      []string
}

func (t *Table) FullName() string { return t.Schema + "." + t.Name }

func (t *Table) Ident() string { return pgx.Identifier{t.Schema, t.Name}.Sanitize() }

func (t *Table) Column(name string) (Column, bool) {
	for _, c := range t.Columns {
		if c.Name == name {
			return c, true
		}
	}
	return Column{}, false
}

// listTables returns ordinary and partitioned (parent) tables in the given
// schemas. Individual partitions are skipped; they are read via the parent.
func listTables(ctx context.Context, conn *pgx.Conn, schemas []string) ([]*Table, error) {
	rows, err := conn.Query(ctx, `
		SELECT n.nspname, c.relname
		FROM pg_class c
		JOIN pg_namespace n ON n.oid = c.relnamespace
		WHERE c.relkind IN ('r', 'p')
		  AND NOT c.relispartition
		  AND n.nspname = ANY($1)
		ORDER BY 1, 2`, schemas)
	if err != nil {
		return nil, err
	}
	var tables []*Table
	for rows.Next() {
		t := &Table{}
		if err := rows.Scan(&t.Schema, &t.Name); err != nil {
			return nil, err
		}
		tables = append(tables, t)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for _, t := range tables {
		if err := describeTable(ctx, conn, t); err != nil {
			return nil, fmt.Errorf("describe %s: %w", t.FullName(), err)
		}
	}
	return tables, nil
}

var errNoTable = errors.New("table does not exist")

// describeTable fills in columns (excluding generated ones) and primary key.
func describeTable(ctx context.Context, conn querier, t *Table) error {
	var oid uint32
	err := conn.QueryRow(ctx, `SELECT coalesce(to_regclass($1)::oid, 0)`, t.Ident()).Scan(&oid)
	if err != nil {
		return err
	}
	if oid == 0 {
		return errNoTable
	}

	rows, err := conn.Query(ctx, `
		SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull, a.atttypid
		FROM pg_attribute a
		WHERE a.attrelid = $1 AND a.attnum > 0 AND NOT a.attisdropped
		  AND a.attgenerated = ''
		ORDER BY a.attnum`, oid)
	if err != nil {
		return err
	}
	t.Columns = nil
	for rows.Next() {
		var c Column
		if err := rows.Scan(&c.Name, &c.Type, &c.NotNull, &c.TypeOID); err != nil {
			return err
		}
		t.Columns = append(t.Columns, c)
	}
	if err := rows.Err(); err != nil {
		return err
	}

	rows, err = conn.Query(ctx, `
		SELECT a.attname
		FROM pg_index i
		CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
		JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
		WHERE i.indrelid = $1 AND i.indisprimary
		ORDER BY k.ord`, oid)
	if err != nil {
		return err
	}
	t.PK, err = pgx.CollectRows(rows, pgx.RowTo[string])
	return err
}

// ensureTargetTable creates the schema and table on the target if missing.
// Only columns, NOT NULL and the primary key are reproduced; defaults,
// foreign keys and other constraints are intentionally left out so the
// target stays a plain replica that can be loaded in any table order.
func ensureTargetTable(ctx context.Context, conn querier, src *Table) error {
	if _, err := conn.Exec(ctx, "CREATE SCHEMA IF NOT EXISTS "+pgx.Identifier{src.Schema}.Sanitize()); err != nil {
		return err
	}
	var defs []string
	for _, c := range src.Columns {
		d := quoteIdent(c.Name) + " " + c.Type
		if c.NotNull {
			d += " NOT NULL"
		}
		defs = append(defs, d)
	}
	if len(src.PK) > 0 {
		defs = append(defs, "PRIMARY KEY ("+identList(src.PK)+")")
	}
	_, err := conn.Exec(ctx, fmt.Sprintf("CREATE TABLE IF NOT EXISTS %s (\n  %s\n)", src.Ident(), strings.Join(defs, ",\n  ")))
	return err
}

func quoteIdent(s string) string { return pgx.Identifier{s}.Sanitize() }

func identList(names []string) string {
	q := make([]string, len(names))
	for i, n := range names {
		q[i] = quoteIdent(n)
	}
	return strings.Join(q, ", ")
}

// quoteLiteral quotes a string as a SQL literal (standard_conforming_strings on).
func quoteLiteral(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
