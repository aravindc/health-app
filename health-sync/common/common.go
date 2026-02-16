package common

import (
	"strconv"
	"strings"

	log "github.com/sirupsen/logrus"
)

func TrendToDirection(trend string) int {
	direction := 0
	switch {
	case trend == "NONE":
		direction = 0
	case trend == "DoubleUp":
		direction = 1
	case trend == "SingleUp":
		direction = 2
	case trend == "FortyFiveUp":
		direction = 3
	case trend == "Flat":
		direction = 4
	case trend == "FortyFiveDown":
		direction = 5
	case trend == "SingleDown":
		direction = 6
	case trend == "DoubleDown":
		direction = 7
	case trend == "NotComputable":
		direction = 8
	case trend == "RATE OUT OF RANGE":
		direction = 9
	default:
		direction = 99
	}
	return direction
}

func TernaryIf[T any](cond bool, vtrue, vfalse T) T {
	if cond {
		return vtrue
	}
	return vfalse
}

func CleanString(input_string string) string {
	return strings.Trim(input_string, "\"")
}

// Remove the function Date() around unixtimestamp
func CleanDateString(input_string string) int64 {
	retval, err := strconv.ParseInt(strings.TrimRight(strings.TrimPrefix(input_string, "Date("), ")"), 10, 64)
	if err != nil {
		log.Info(err)
	}
	return retval
}
