package main

import (
	"context"
	"fmt"
	"log/slog"
	"slices"
	"strings"

	"github.com/jackc/pgx/v5"
)

// typeSyncer replicates user-defined column types (enums, domains,
// composites, and arrays of them) from source to target. It runs outside
// the per-table transaction: a newly added enum value cannot be used in the
// same transaction that added it.
type typeSyncer struct {
	src, tgt *pgx.Conn
	dryRun   bool
	done     map[uint32]bool // source type OIDs already handled this run
}

func newTypeSyncer(src, tgt *pgx.Conn, dryRun bool) *typeSyncer {
	return &typeSyncer{src: src, tgt: tgt, dryRun: dryRun, done: map[uint32]bool{}}
}

type pgType struct {
	oid       uint32
	schema    string
	name      string
	kind      string // typtype: b=base, c=composite, d=domain, e=enum, r=range, m=multirange
	elem      uint32 // element type for arrays
	category  string
	baseType  uint32 // domains
	baseDecl  string // domains: format_type(typbasetype, typtypmod)
	relid     uint32 // composites
	extension *string
}

func (t *pgType) ident() string { return pgx.Identifier{t.schema, t.name}.Sanitize() }

// ensureTable makes sure every user-defined type used by tbl exists on target.
func (s *typeSyncer) ensureTable(ctx context.Context, tbl *Table) error {
	for _, c := range tbl.Columns {
		if err := s.ensure(ctx, c.TypeOID); err != nil {
			return fmt.Errorf("column %q type %s: %w", c.Name, c.Type, err)
		}
	}
	return nil
}

func (s *typeSyncer) ensure(ctx context.Context, oid uint32) error {
	if s.done[oid] {
		return nil
	}
	s.done[oid] = true

	t := &pgType{oid: oid}
	err := s.src.QueryRow(ctx, `
		SELECT n.nspname, t.typname, t.typtype::text, t.typelem, t.typcategory::text,
		       t.typbasetype, format_type(t.typbasetype, t.typtypmod), t.typrelid,
		       (SELECT e.extname FROM pg_depend d JOIN pg_extension e ON e.oid = d.refobjid
		        WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e')
		FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
		WHERE t.oid = $1`, oid).Scan(&t.schema, &t.name, &t.kind, &t.elem, &t.category,
		&t.baseType, &t.baseDecl, &t.relid, &t.extension)
	if err != nil {
		return err
	}

	switch {
	case t.category == "A" && t.elem != 0: // array: the element type is what matters
		return s.ensure(ctx, t.elem)
	case t.schema == "pg_catalog" || t.schema == "information_schema":
		return nil
	case t.extension != nil:
		exists, err := s.targetHas(ctx, t)
		if err != nil || exists {
			return err
		}
		return fmt.Errorf("type %s comes from extension %q; run CREATE EXTENSION %s on the target", t.ident(), *t.extension, quoteIdent(*t.extension))
	}

	switch t.kind {
	case "e":
		return s.ensureEnum(ctx, t)
	case "d":
		if err := s.ensure(ctx, t.baseType); err != nil {
			return err
		}
		return s.createIfMissing(ctx, t, fmt.Sprintf("CREATE DOMAIN %s AS %s", t.ident(), t.baseDecl))
	case "c":
		return s.ensureComposite(ctx, t)
	default:
		return fmt.Errorf("unsupported user-defined type %s (typtype %q); create it on the target manually", t.ident(), t.kind)
	}
}

func (s *typeSyncer) targetHas(ctx context.Context, t *pgType) (bool, error) {
	var ok bool
	err := s.tgt.QueryRow(ctx, "SELECT to_regtype($1) IS NOT NULL", t.ident()).Scan(&ok)
	return ok, err
}

func (s *typeSyncer) exec(ctx context.Context, t *pgType, sql string) error {
	if s.dryRun {
		slog.Info("would run on target", "type", t.ident(), "sql", sql)
		return nil
	}
	if _, err := s.tgt.Exec(ctx, sql); err != nil {
		return err
	}
	slog.Info("replicated type", "type", t.ident(), "sql", sql)
	return nil
}

func (s *typeSyncer) createIfMissing(ctx context.Context, t *pgType, create string) error {
	exists, err := s.targetHas(ctx, t)
	if err != nil || exists {
		return err
	}
	if !s.dryRun {
		if _, err := s.tgt.Exec(ctx, "CREATE SCHEMA IF NOT EXISTS "+quoteIdent(t.schema)); err != nil {
			return err
		}
	}
	return s.exec(ctx, t, create)
}

// ensureEnum creates the enum, or adds labels the source has gained since.
func (s *typeSyncer) ensureEnum(ctx context.Context, t *pgType) error {
	labels, err := enumLabels(ctx, s.src, "$1::oid", t.oid)
	if err != nil {
		return err
	}
	exists, err := s.targetHas(ctx, t)
	if err != nil {
		return err
	}
	if !exists {
		quoted := make([]string, len(labels))
		for i, l := range labels {
			quoted[i] = quoteLiteral(l)
		}
		return s.createIfMissing(ctx, t, fmt.Sprintf("CREATE TYPE %s AS ENUM (%s)", t.ident(), strings.Join(quoted, ", ")))
	}

	have, err := enumLabels(ctx, s.tgt, "to_regtype($1)", t.ident())
	if err != nil {
		return err
	}
	for i, l := range labels {
		if slices.Contains(have, l) {
			continue
		}
		// Keep source ordering: place after the previous source label, or
		// before the first existing one if this is the first label.
		pos := ""
		if i > 0 {
			pos = " AFTER " + quoteLiteral(labels[i-1])
		} else if len(have) > 0 {
			pos = " BEFORE " + quoteLiteral(have[0])
		}
		if err := s.exec(ctx, t, fmt.Sprintf("ALTER TYPE %s ADD VALUE IF NOT EXISTS %s%s", t.ident(), quoteLiteral(l), pos)); err != nil {
			return err
		}
		have = append(have, l)
	}
	return nil
}

func enumLabels(ctx context.Context, conn *pgx.Conn, typeExpr string, arg any) ([]string, error) {
	rows, err := conn.Query(ctx, "SELECT enumlabel FROM pg_enum WHERE enumtypid = "+typeExpr+" ORDER BY enumsortorder", arg)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowTo[string])
}

func (s *typeSyncer) ensureComposite(ctx context.Context, t *pgType) error {
	rows, err := s.src.Query(ctx, `
		SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.atttypid
		FROM pg_attribute a
		WHERE a.attrelid = $1 AND a.attnum > 0 AND NOT a.attisdropped
		ORDER BY a.attnum`, t.relid)
	if err != nil {
		return err
	}
	var fields []string
	var oids []uint32
	for rows.Next() {
		var name, decl string
		var oid uint32
		if err := rows.Scan(&name, &decl, &oid); err != nil {
			return err
		}
		fields = append(fields, quoteIdent(name)+" "+decl)
		oids = append(oids, oid)
	}
	if err := rows.Err(); err != nil {
		return err
	}
	for _, oid := range oids {
		if err := s.ensure(ctx, oid); err != nil {
			return err
		}
	}
	return s.createIfMissing(ctx, t, fmt.Sprintf("CREATE TYPE %s AS (%s)", t.ident(), strings.Join(fields, ", ")))
}
