package main

import (
    "database/sql"
    "encoding/json"
    "log"
    "net/http"
    "os"
    "strconv"
    "time"

    "github.com/gorilla/mux"
    _ "github.com/lib/pq"
    "github.com/prometheus/client_golang/prometheus"
    "github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
    requestDuration = prometheus.NewHistogramVec(
        prometheus.HistogramOpts{
            Name:    "http_request_duration_seconds",
            Help:    "HTTP request durations",
            Buckets: prometheus.DefBuckets,
        },
        []string{"method", "path", "status"},
    )

    requestCount = prometheus.NewCounterVec(
        prometheus.CounterOpts{
            Name: "http_requests_total",
            Help: "Total HTTP requests",
        },
        []string{"method", "path", "status"},
    )

    dbQueryDuration = prometheus.NewHistogram(
        prometheus.HistogramOpts{
            Name:    "db_query_duration_seconds",
            Help:    "Database query durations",
            Buckets: prometheus.DefBuckets,
        },
    )
)

func init() {
    prometheus.MustRegister(requestDuration, requestCount, dbQueryDuration)
}

type Server struct {
    db *sql.DB
}

func main() {
    dsn := os.Getenv("DATABASE_URL")
    if dsn == "" {
        dsn = "postgres://demo:demo@localhost:5432/demo?sslmode=disable"
    }

    db, err := sql.Open("postgres", dsn)
    if err != nil {
        log.Fatal(err)
    }
    for i := 0; i < 10; i++ {
        if err := db.Ping(); err == nil {
            break
        }
        log.Println("waiting for db...", i)
        time.Sleep(1 * time.Second)
    }

    s := &Server{db: db}

    r := mux.NewRouter()
    api := r.PathPrefix("/api").Subrouter()

    api.HandleFunc("/users", s.createUser).Methods("POST")
    api.HandleFunc("/users", s.listUsers).Methods("GET")
    api.HandleFunc("/users/{id}", s.getUser).Methods("GET")
    api.HandleFunc("/users/{id}", s.updateUser).Methods("PUT")
    api.HandleFunc("/users/{id}", s.deleteUser).Methods("DELETE")

    api.HandleFunc("/orders", s.createOrder).Methods("POST")
    api.HandleFunc("/orders", s.listOrders).Methods("GET")
    api.HandleFunc("/orders/{id}", s.getOrder).Methods("GET")
    api.HandleFunc("/orders/{id}", s.updateOrder).Methods("PUT")
    api.HandleFunc("/orders/{id}", s.deleteOrder).Methods("DELETE")

    r.Handle("/metrics", promhttp.Handler())

    handler := instrumentHandler(r)

    addr := ":8081"
    log.Println("listening on", addr)
    log.Fatal(http.ListenAndServe(addr, handler))
}

func instrumentHandler(h http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        start := time.Now()
        rw := &statusRecorder{ResponseWriter: w, status: 200}
        h.ServeHTTP(rw, r)
        dur := time.Since(start).Seconds()
        path := r.URL.Path
        requestDuration.WithLabelValues(r.Method, path, strconv.Itoa(rw.status)).Observe(dur)
        requestCount.WithLabelValues(r.Method, path, strconv.Itoa(rw.status)).Inc()
    })
}

type statusRecorder struct {
    http.ResponseWriter
    status int
}

func (r *statusRecorder) WriteHeader(code int) {
    r.status = code
    r.ResponseWriter.WriteHeader(code)
}

type User struct {
    ID        int       `json:"id"`
    Name      string    `json:"name"`
    Email     string    `json:"email"`
    CreatedAt time.Time `json:"created_at"`
}

type Order struct {
    ID          int       `json:"id"`
    UserID      int       `json:"user_id"`
    Amount      float64   `json:"amount"`
    Description string    `json:"description"`
    CreatedAt   time.Time `json:"created_at"`
}

func (s *Server) createUser(w http.ResponseWriter, r *http.Request) {
    var u User
    if err := json.NewDecoder(r.Body).Decode(&u); err != nil {
        http.Error(w, "bad request", http.StatusBadRequest)
        return
    }
    start := time.Now()
    err := s.db.QueryRow("INSERT INTO users (name, email) VALUES ($1, $2) RETURNING id, created_at", u.Name, u.Email).Scan(&u.ID, &u.CreatedAt)
    dbQueryDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(u)
}

func (s *Server) listUsers(w http.ResponseWriter, r *http.Request) {
    start := time.Now()
    rows, err := s.db.Query("SELECT id, name, email, created_at FROM users ORDER BY id DESC LIMIT 100")
    dbQueryDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    defer rows.Close()
    users := []User{}
    for rows.Next() {
        var u User
        if err := rows.Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt); err != nil {
            http.Error(w, err.Error(), http.StatusInternalServerError)
            return
        }
        users = append(users, u)
    }
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(users)
}

func (s *Server) getUser(w http.ResponseWriter, r *http.Request) {
    vars := mux.Vars(r)
    idStr := vars["id"]
    id, _ := strconv.Atoi(idStr)
    start := time.Now()
    var u User
    err := s.db.QueryRow("SELECT id, name, email, created_at FROM users WHERE id=$1", id).Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt)
    dbQueryDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        if err == sql.ErrNoRows {
            http.Error(w, "not found", http.StatusNotFound)
            return
        }
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(u)
}

func (s *Server) updateUser(w http.ResponseWriter, r *http.Request) {
    vars := mux.Vars(r)
    idStr := vars["id"]
    id, _ := strconv.Atoi(idStr)
    var u User
    if err := json.NewDecoder(r.Body).Decode(&u); err != nil {
        http.Error(w, "bad request", http.StatusBadRequest)
        return
    }
    start := time.Now()
    _, err := s.db.Exec("UPDATE users SET name=$1, email=$2 WHERE id=$3", u.Name, u.Email, id)
    dbQueryDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    w.WriteHeader(http.StatusNoContent)
}

func (s *Server) deleteUser(w http.ResponseWriter, r *http.Request) {
    vars := mux.Vars(r)
    idStr := vars["id"]
    id, _ := strconv.Atoi(idStr)
    start := time.Now()
    _, err := s.db.Exec("DELETE FROM users WHERE id=$1", id)
    dbQueryDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    w.WriteHeader(http.StatusNoContent)
}

func (s *Server) createOrder(w http.ResponseWriter, r *http.Request) {
    var o Order
    if err := json.NewDecoder(r.Body).Decode(&o); err != nil {
        http.Error(w, "bad request", http.StatusBadRequest)
        return
    }
    start := time.Now()
    err := s.db.QueryRow("INSERT INTO orders (user_id, amount, description) VALUES ($1,$2,$3) RETURNING id, created_at", o.UserID, o.Amount, o.Description).Scan(&o.ID, &o.CreatedAt)
    dbQueryDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(o)
}

func (s *Server) listOrders(w http.ResponseWriter, r *http.Request) {
    start := time.Now()
    rows, err := s.db.Query("SELECT id, user_id, amount, description, created_at FROM orders ORDER BY id DESC LIMIT 100")
    dbQueryDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    defer rows.Close()
    orders := []Order{}
    for rows.Next() {
        var o Order
        if err := rows.Scan(&o.ID, &o.UserID, &o.Amount, &o.Description, &o.CreatedAt); err != nil {
            http.Error(w, err.Error(), http.StatusInternalServerError)
            return
        }
        orders = append(orders, o)
    }
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(orders)
}

func (s *Server) getOrder(w http.ResponseWriter, r *http.Request) {
    vars := mux.Vars(r)
    idStr := vars["id"]
    id, _ := strconv.Atoi(idStr)
    start := time.Now()
    var o Order
    err := s.db.QueryRow("SELECT id, user_id, amount, description, created_at FROM orders WHERE id=$1", id).Scan(&o.ID, &o.UserID, &o.Amount, &o.Description, &o.CreatedAt)
    dbQueryDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        if err == sql.ErrNoRows {
            http.Error(w, "not found", http.StatusNotFound)
            return
        }
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(o)
}

func (s *Server) updateOrder(w http.ResponseWriter, r *http.Request) {
    vars := mux.Vars(r)
    idStr := vars["id"]
    id, _ := strconv.Atoi(idStr)
    var o Order
    if err := json.NewDecoder(r.Body).Decode(&o); err != nil {
        http.Error(w, "bad request", http.StatusBadRequest)
        return
    }
    start := time.Now()
    _, err := s.db.Exec("UPDATE orders SET user_id=$1, amount=$2, description=$3 WHERE id=$4", o.UserID, o.Amount, o.Description, id)
    dbQueryDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    w.WriteHeader(http.StatusNoContent)
}

func (s *Server) deleteOrder(w http.ResponseWriter, r *http.Request) {
    vars := mux.Vars(r)
    idStr := vars["id"]
    id, _ := strconv.Atoi(idStr)
    start := time.Now()
    _, err := s.db.Exec("DELETE FROM orders WHERE id=$1", id)
    dbQueryDuration.Observe(time.Since(start).Seconds())
    if err != nil {
        http.Error(w, err.Error(), http.StatusInternalServerError)
        return
    }
    w.WriteHeader(http.StatusNoContent)
}
