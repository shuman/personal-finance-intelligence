"""
Machine Learning router - train and manage ML models.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Transaction
from app.ml.categorizer import get_categorizer

router = APIRouter(prefix="/api/ml", tags=["machine-learning"])


@router.post("/train")
async def train_model(
    db: AsyncSession = Depends(get_db)
):
    """
    Train the ML model using all transactions with categories.

    The model learns from:
    - Transactions you've manually categorized
    - Pattern of merchant names and descriptions
    - Your category preferences

    Call this endpoint after:
    - Uploading new statements
    - Manually correcting categories
    - Periodically to improve accuracy
    """
    # Get all transactions with categories
    result = await db.execute(
        select(Transaction).where(Transaction.merchant_category.isnot(None))
    )
    transactions = result.scalars().all()

    if not transactions:
        raise HTTPException(
            status_code=400,
            detail="No categorized transactions found. Upload statements first."
        )

    # Convert to dict format
    training_data = [
        {
            'description_raw': t.description_raw,
            'merchant_name': t.merchant_name,
            'merchant_category': t.merchant_category
        }
        for t in transactions
    ]

    # Train the model
    categorizer = get_categorizer()
    result = categorizer.train_from_transactions(training_data)

    if not result['success']:
        raise HTTPException(status_code=400, detail=result['message'])

    return {
        "success": True,
        "message": f"Model trained successfully with {result['samples']} transactions",
        "stats": {
            "samples": result['samples'],
            "categories": result['categories'],
            "accuracy": f"{result['accuracy'] * 100:.1f}%"
        }
    }


@router.get("/stats")
async def get_model_stats():
    """
    Get ML model statistics and performance metrics.

    Shows:
    - Number of training samples
    - Categories learned
    - Estimated accuracy
    - Last training time
    """
    categorizer = get_categorizer()
    stats = categorizer.get_stats()

    return {
        "model_status": "trained" if stats['can_predict'] else "not_trained",
        "trained_samples": stats['trained_samples'],
        "categories_learned": len(stats.get('categories_learned', [])),
        "categories": stats.get('categories_learned', []),
        "accuracy": f"{stats['accuracy_estimate'] * 100:.1f}%" if stats['accuracy_estimate'] > 0 else "N/A",
        "last_trained": stats['last_trained'],
        "can_predict": stats['can_predict']
    }


@router.post("/predict")
async def predict_category(
    description: str,
    merchant_name: str = None
):
    """
    Predict category for a transaction description.

    Useful for testing the model or getting predictions for new transactions.

    Args:
        description: Transaction description
        merchant_name: Optional merchant name

    Returns:
        Predicted category and confidence score
    """
    categorizer = get_categorizer()

    if not categorizer.pipeline:
        raise HTTPException(
            status_code=400,
            detail="Model not trained yet. Train the model first with /api/ml/train"
        )

    category, confidence = categorizer.predict_category(description, merchant_name)

    if category is None:
        return {
            "category": "Unknown (low confidence)",
            "confidence": f"{confidence * 100:.1f}%",
            "message": "Confidence too low, would use rule-based fallback"
        }

    return {
        "category": category,
        "confidence": f"{confidence * 100:.1f}%",
        "message": "High confidence prediction"
    }


@router.put("/transactions/{transaction_id}/category")
async def update_transaction_category(
    transaction_id: int,
    category: str,
    retrain: bool = True,
    db: AsyncSession = Depends(get_db)
):
    """
    Update a transaction's category manually.

    This is how you teach the model! When you correct a category,
    the model can learn from your correction.

    Args:
        transaction_id: Transaction to update
        category: New category
        retrain: Whether to retrain model after update (default: True)
    """
    # Get transaction
    result = await db.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )
    transaction = result.scalar_one_or_none()

    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Update category
    old_category = transaction.merchant_category
    transaction.merchant_category = category
    await db.commit()

    response = {
        "success": True,
        "transaction_id": transaction_id,
        "old_category": old_category,
        "new_category": category,
        "description": transaction.description_raw
    }

    # Optionally retrain model
    if retrain:
        result = await db.execute(
            select(Transaction).where(Transaction.merchant_category.isnot(None))
        )
        all_transactions = result.scalars().all()

        training_data = [
            {
                'description_raw': t.description_raw,
                'merchant_name': t.merchant_name,
                'merchant_category': t.merchant_category
            }
            for t in all_transactions
        ]

        categorizer = get_categorizer()
        train_result = categorizer.train_from_transactions(training_data)

        response['model_retrained'] = train_result['success']
        response['model_accuracy'] = f"{train_result.get('accuracy', 0) * 100:.1f}%"

    return response
