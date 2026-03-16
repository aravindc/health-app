package mongo

import (
	"context"
	"fmt"
	"health-mongo-sync/model"
	"time"

	log "github.com/sirupsen/logrus"
	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"
)

// nsDoc is the Nightscout MongoDB document shape.
// Nightscout stores sgv in mg/dL; date is Unix milliseconds.
type nsDoc struct {
	Date      int64              `bson:"date"`       // Unix ms
	Sgv       int                `bson:"sgv"`
	Direction string             `bson:"direction"`
	ID        primitive.ObjectID `bson:"_id"`
}

// Connect returns a connected MongoDB client.
func Connect(ctx context.Context, uri string) (*mongo.Client, error) {
	clientOpts := options.Client().ApplyURI(uri)
	client, err := mongo.Connect(ctx, clientOpts)
	if err != nil {
		return nil, fmt.Errorf("mongo connect: %w", err)
	}
	if err := client.Ping(ctx, nil); err != nil {
		return nil, fmt.Errorf("mongo ping: %w", err)
	}
	log.Info("MongoDB connection established")
	return client, nil
}

// FetchRange fetches all Nightscout entries from MongoDB whose date
// falls within [from, to]. A single query is issued — no pagination needed
// because the full result set is held in memory before Postgres insert.
func FetchRange(ctx context.Context, client *mongo.Client, dbName, collection string, from, to time.Time) ([]model.NsEntry, error) {
	coll := client.Database(dbName).Collection(collection)

	fromMs := from.UnixMilli()
	toMs := to.UnixMilli()

	filter := bson.M{
		"date": bson.M{
			"$gte": fromMs,
			"$lte": toMs,
		},
	}
	// Only fetch fields we actually need — reduces wire transfer.
	proj := options.Find().SetProjection(bson.M{"date": 1, "sgv": 1, "direction": 1})

	cursor, err := coll.Find(ctx, filter, proj)
	if err != nil {
		return nil, fmt.Errorf("mongo find: %w", err)
	}
	defer cursor.Close(ctx)

	var docs []nsDoc
	if err := cursor.All(ctx, &docs); err != nil {
		return nil, fmt.Errorf("mongo cursor decode: %w", err)
	}

	log.Infof("Fetched %d documents from MongoDB (%s.%s)", len(docs), dbName, collection)

	entries := make([]model.NsEntry, 0, len(docs))
	for _, d := range docs {
		if d.Sgv <= 0 || d.Date <= 0 {
			continue // skip malformed records
		}
		t := time.UnixMilli(d.Date).UTC()
		entries = append(entries, model.NsEntry{
			Sgv:        d.Sgv,
			NsTime:     d.Date,
			NsDatetime: t,
			Trend:      directionToInt(d.Direction),
			Utcoffset:  0,
			Systime:    t,
		})
	}
	return entries, nil
}

func directionToInt(d string) int {
	switch d {
	case "DoubleUp":
		return 1
	case "SingleUp":
		return 2
	case "FortyFiveUp":
		return 3
	case "Flat":
		return 4
	case "FortyFiveDown":
		return 5
	case "SingleDown":
		return 6
	case "DoubleDown":
		return 7
	case "NOT COMPUTABLE":
		return 8
	case "RATE OUT OF RANGE":
		return 9
	default:
		return 0
	}
}
