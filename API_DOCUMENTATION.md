# API Documentation

## Base URL
```
http://localhost:8000
```

## Endpoints

### 1. Upload & Processing

#### POST `/api/upload`
Upload and process a credit card statement PDF.

**Form Data:**
- `file`: PDF file (multipart/form-data)
- `password`: PDF password (optional)
- `bank`: Bank identifier (e.g., "amex")

**Response:**
```json
{
  "statement_id": 1,
  "account_number": "100000087858",
  "statement_date": "2025-07-23",
  "transactions_added": 38,
  "transactions_skipped": 0,
  "fees_added": 2,
  "total_amount": 45670.50
}
```

**Error Responses:**
```json
{
  "detail": "Missing required fields: statement_period_from, statement_period_to"
}
```

---

### 2. Statements

#### GET `/api/statements`
List all statements with pagination.

**Query Parameters:**
- `skip`: Offset for pagination (default: 0)
- `limit`: Number of results (default: 100)

**Response:**
```json
[
  {
    "id": 1,
    "account_number": "100000087858",
    "statement_date": "2025-07-23",
    "statement_period_from": "2025-06-24",
    "statement_period_to": "2025-07-23",
    "total_amount_due": 45670.50,
    "minimum_amount_due": 4567.05,
    "credit_limit": 500000.00,
    "available_credit": 454329.50,
    "filename": "CBL_AMEX_Gold_100000087858_2390193_23072025.pdf"
  }
]
```

#### GET `/api/statements/{statement_id}`
Get details of a specific statement.

**Response:**
```json
{
  "id": 1,
  "account_number": "100000087858",
  "statement_date": "2025-07-23",
  "total_amount_due": 45670.50,
  "transactions": [...],
  "fees": [...],
  "rewards": {...}
}
```

#### DELETE `/api/statements/{statement_id}`
Delete a statement and all associated data.

**Response:**
```json
{
  "message": "Statement deleted successfully"
}
```

---

### 3. Transactions

#### GET `/api/transactions/search`
Search transactions across all statements.

**Query Parameters:**
- `statement_id`: Filter by statement (optional)
- `date`: Filter by transaction date (YYYY-MM-DD)
- `description`: Partial match search (case-insensitive)
- `amount`: Filter by exact amount
- `category`: Filter by category
- `skip`: Pagination offset (default: 0)
- `limit`: Results per page (default: 50)

**Response:**
```json
[
  {
    "id": 1,
    "statement_id": 1,
    "transaction_date": "2025-07-15",
    "description_raw": "SWIGGY BANGALORE",
    "merchant_name": "SWIGGY",
    "merchant_category": "Food & Dining",
    "amount": 450.00,
    "transaction_type": "DEBIT",
    "statement_filename": "CBL_AMEX_Gold_100000087858_2390193_23072025.pdf"
  }
]
```

#### GET `/api/transactions/export/csv`
Export transactions to CSV file.

**Query Parameters:** (same as search)

**Response:** CSV file download

#### PUT `/api/transactions/{transaction_id}/category`
Update transaction category.

**Request Body:**
```json
{
  "category": "Food & Dining"
}
```

**Response:**
```json
{
  "id": 1,
  "merchant_category": "Food & Dining",
  "message": "Category updated successfully"
}
```

---

### 4. Machine Learning

#### POST `/api/ml/train`
Train the ML categorizer on all existing transactions.

**Response:**
```json
{
  "message": "Model trained successfully",
  "samples_used": 245,
  "categories": ["Food & Dining", "Shopping", "Fuel", "Entertainment"],
  "accuracy": 0.87,
  "timestamp": "2025-12-11T14:30:00"
}
```

#### GET `/api/ml/stats`
Get ML model statistics.

**Response:**
```json
{
  "is_trained": true,
  "training_samples": 245,
  "categories": 8,
  "last_trained": "2025-12-11T14:30:00",
  "accuracy": 0.87,
  "model_file": "categorizer_model.pkl"
}
```

#### POST `/api/ml/predict`
Test prediction for a transaction description.

**Request Body:**
```json
{
  "description": "SWIGGY BANGALORE"
}
```

**Response:**
```json
{
  "description": "SWIGGY BANGALORE",
  "predicted_category": "Food & Dining",
  "confidence": 0.92,
  "all_probabilities": {
    "Food & Dining": 0.92,
    "Shopping": 0.05,
    "Other": 0.03
  }
}
```

#### PUT `/api/transactions/{transaction_id}/category`
Update category and optionally retrain model.

**Query Parameters:**
- `retrain`: Boolean, trigger model retraining (default: false)

**Request Body:**
```json
{
  "category": "Food & Dining"
}
```

**Response:**
```json
{
  "id": 1,
  "merchant_category": "Food & Dining",
  "message": "Category updated and model retrained"
}
```

---

### 5. Analytics

#### GET `/api/analytics/category-summary`
Get spending summary by category.

**Query Parameters:**
- `statement_id`: Filter by statement (optional)
- `start_date`: Start date (YYYY-MM-DD)
- `end_date`: End date (YYYY-MM-DD)

**Response:**
```json
{
  "categories": [
    {
      "category": "Food & Dining",
      "total_amount": 12500.00,
      "transaction_count": 45,
      "percentage": 27.5
    },
    {
      "category": "Shopping",
      "total_amount": 8900.00,
      "transaction_count": 23,
      "percentage": 19.6
    }
  ],
  "total_spent": 45400.00,
  "date_range": {
    "from": "2025-06-24",
    "to": "2025-07-23"
  }
}
```

#### GET `/api/analytics/monthly-trends`
Get spending trends by month.

**Response:**
```json
{
  "months": [
    {
      "month": "2025-07",
      "total_spent": 45670.50,
      "transaction_count": 38,
      "average_transaction": 1201.86
    },
    {
      "month": "2025-08",
      "total_spent": 52100.00,
      "transaction_count": 42,
      "average_transaction": 1240.48
    }
  ]
}
```

---

## Error Codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request (validation error) |
| 404 | Not Found |
| 409 | Conflict (duplicate) |
| 422 | Unprocessable Entity |
| 500 | Internal Server Error |

## Common Error Responses

### Validation Error
```json
{
  "detail": [
    {
      "loc": ["body", "category"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

### Duplicate Transaction
```json
{
  "detail": "Duplicate transaction detected: same date, amount, and description already exists"
}
```

### Missing Required Fields
```json
{
  "detail": "Missing required fields: statement_period_from, statement_period_to. Check if these dates are present in your PDF."
}
```

---

## Rate Limiting

Currently no rate limiting is implemented. For production use, consider adding rate limiting middleware.

## Authentication

Currently no authentication is required. For production use with sensitive data:
- Implement OAuth2/JWT authentication
- Add API key validation
- Use HTTPS only
- Implement CORS properly

---

## Example Usage with cURL

### Upload a statement
```bash
curl -X POST http://localhost:8000/api/upload \
  -F "file=@statement.pdf" \
  -F "password=mypassword" \
  -F "bank=amex"
```

### Search transactions
```bash
curl "http://localhost:8000/api/transactions/search?description=swiggy&limit=10"
```

### Train ML model
```bash
curl -X POST http://localhost:8000/api/ml/train
```

### Update transaction category
```bash
curl -X PUT http://localhost:8000/api/transactions/1/category \
  -H "Content-Type: application/json" \
  -d '{"category": "Food & Dining"}'
```

### Export to CSV
```bash
curl "http://localhost:8000/api/transactions/export/csv?category=Food%20%26%20Dining" \
  -o transactions.csv
```

---

## Python Client Example

```python
import requests

BASE_URL = "http://localhost:8000"

# Upload statement
with open("statement.pdf", "rb") as f:
    response = requests.post(
        f"{BASE_URL}/api/upload",
        files={"file": f},
        data={"password": "mypassword", "bank": "amex"}
    )
    print(response.json())

# Search transactions
response = requests.get(
    f"{BASE_URL}/api/transactions/search",
    params={"description": "swiggy", "limit": 10}
)
print(response.json())

# Train model
response = requests.post(f"{BASE_URL}/api/ml/train")
print(response.json())

# Predict category
response = requests.post(
    f"{BASE_URL}/api/ml/predict",
    json={"description": "SWIGGY BANGALORE"}
)
print(response.json())
```

---

## WebSocket Support

Not currently implemented. Future enhancement for real-time upload progress.
