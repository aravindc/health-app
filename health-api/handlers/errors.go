package handlers

import (
	"log/slog"
	"net/http"

	"github.com/gin-gonic/gin"
)

// serverError logs err with the request's route and answers 500 with a
// generic message, so database and driver details (SQL, table names,
// hosts) stay in the server log instead of reaching the client.
func serverError(c *gin.Context, err error) {
	slog.Error("Request failed", "method", c.Request.Method, "route", c.FullPath(), "error", err)
	c.JSON(http.StatusInternalServerError, gin.H{"detail": "Internal server error"})
}
