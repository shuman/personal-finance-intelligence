"""
Intelligent Transaction Categorizer using Machine Learning.

This module learns from your manual category corrections to improve predictions.
It uses:
- TF-IDF: Converts transaction descriptions into numerical features
- Naive Bayes: Fast, efficient classifier for text data
- Incremental learning: Updates model as you correct categories
"""
import os
import pickle
import json
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from pathlib import Path

# scikit-learn imports
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
import numpy as np


class TransactionCategorizer:
    """
    ML-powered transaction categorizer that learns from your corrections.

    How it works:
    1. Uses rule-based categorization for initial predictions
    2. Learns from your manual corrections
    3. Improves predictions over time
    4. Falls back to rules when confidence is low
    """

    def __init__(self, model_dir: str = "./models"):
        """
        Initialize the categorizer.

        Args:
            model_dir: Directory to save/load the trained model
        """
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        self.model_path = self.model_dir / "category_model.pkl"
        self.stats_path = self.model_dir / "model_stats.json"

        # The ML pipeline: TF-IDF -> Naive Bayes
        self.pipeline: Optional[Pipeline] = None
        self.categories: List[str] = []
        self.stats = {
            "trained_samples": 0,
            "last_trained": None,
            "accuracy_estimate": 0.0,
            "categories_learned": []
        }

        # Load existing model if available
        self._load_model()

    def _load_model(self):
        """Load saved model and statistics."""
        if self.model_path.exists():
            try:
                with open(self.model_path, 'rb') as f:
                    data = pickle.load(f)
                    self.pipeline = data['pipeline']
                    self.categories = data['categories']
                print(f"✓ ML Model loaded: {len(self.categories)} categories, {self.stats['trained_samples']} samples")
            except Exception as e:
                print(f"Warning: Could not load model: {e}")

        if self.stats_path.exists():
            try:
                with open(self.stats_path, 'r') as f:
                    self.stats = json.load(f)
            except Exception as e:
                print(f"Warning: Could not load stats: {e}")

    def _save_model(self):
        """Save model and statistics."""
        try:
            with open(self.model_path, 'wb') as f:
                pickle.dump({
                    'pipeline': self.pipeline,
                    'categories': self.categories
                }, f)

            with open(self.stats_path, 'w') as f:
                json.dump(self.stats, f, indent=2)

            print(f"✓ ML Model saved: {len(self.categories)} categories, {self.stats['trained_samples']} samples")
        except Exception as e:
            print(f"Warning: Could not save model: {e}")

    def train_from_transactions(self, transactions: List[Dict]) -> Dict:
        """
        Train or update the model from transaction data.

        Args:
            transactions: List of transaction dicts with 'description_raw' and 'merchant_category'

        Returns:
            Training statistics
        """
        # Filter transactions that have manual categories
        training_data = [
            (self._prepare_text(t['description_raw']), t['merchant_category'])
            for t in transactions
            if t.get('merchant_category') and t.get('description_raw')
        ]

        if len(training_data) < 5:  # Need at least 5 samples
            return {
                "success": False,
                "message": "Need at least 5 categorized transactions to train",
                "samples": len(training_data)
            }

        # Separate features and labels
        X = [text for text, _ in training_data]
        y = [category for _, category in training_data]

        # Get unique categories
        self.categories = sorted(set(y))

        # Create or update pipeline
        if self.pipeline is None:
            self.pipeline = Pipeline([
                ('tfidf', TfidfVectorizer(
                    max_features=500,  # Limit features for efficiency
                    ngram_range=(1, 2),  # Use single words and pairs
                    lowercase=True,
                    stop_words='english'
                )),
                ('classifier', MultinomialNB(alpha=0.1))  # Naive Bayes classifier
            ])

        # Train the model
        self.pipeline.fit(X, y)

        # Update statistics
        self.stats['trained_samples'] = len(training_data)
        self.stats['last_trained'] = datetime.now().isoformat()
        self.stats['categories_learned'] = self.categories

        # Estimate accuracy (simple train accuracy for now)
        if len(training_data) > 10:
            predictions = self.pipeline.predict(X)
            accuracy = np.mean([pred == true for pred, true in zip(predictions, y)])
            self.stats['accuracy_estimate'] = float(accuracy)

        # Save the model
        self._save_model()

        return {
            "success": True,
            "message": "Model trained successfully",
            "samples": len(training_data),
            "categories": len(self.categories),
            "accuracy": self.stats['accuracy_estimate']
        }

    def predict_category(
        self,
        description: str,
        merchant_name: Optional[str] = None,
        min_confidence: float = 0.4
    ) -> Tuple[str, float]:
        """
        Predict category for a transaction.

        Args:
            description: Transaction description
            merchant_name: Merchant name (optional, used to enhance prediction)
            min_confidence: Minimum confidence threshold (0.0 to 1.0)

        Returns:
            Tuple of (predicted_category, confidence_score)
        """
        if self.pipeline is None or not self.categories:
            # Model not trained yet, return None to use rule-based
            return None, 0.0

        # Prepare text
        text = self._prepare_text(description, merchant_name)

        try:
            # Get prediction probabilities
            proba = self.pipeline.predict_proba([text])[0]

            # Get best prediction
            best_idx = np.argmax(proba)
            confidence = float(proba[best_idx])
            predicted_category = self.categories[best_idx]

            # Only return prediction if confidence is high enough
            if confidence >= min_confidence:
                return predicted_category, confidence
            else:
                return None, confidence  # Low confidence, use rule-based fallback

        except Exception as e:
            print(f"Prediction error: {e}")
            return None, 0.0

    def predict_batch(
        self,
        transactions: List[Dict],
        min_confidence: float = 0.4
    ) -> List[Tuple[str, float]]:
        """
        Predict categories for multiple transactions efficiently.

        Args:
            transactions: List of transaction dicts
            min_confidence: Minimum confidence threshold

        Returns:
            List of (category, confidence) tuples
        """
        if self.pipeline is None:
            return [(None, 0.0)] * len(transactions)

        texts = [
            self._prepare_text(t['description_raw'], t.get('merchant_name'))
            for t in transactions
        ]

        try:
            probabilities = self.pipeline.predict_proba(texts)
            results = []

            for proba in probabilities:
                best_idx = np.argmax(proba)
                confidence = float(proba[best_idx])

                if confidence >= min_confidence:
                    category = self.categories[best_idx]
                    results.append((category, confidence))
                else:
                    results.append((None, confidence))

            return results
        except Exception as e:
            print(f"Batch prediction error: {e}")
            return [(None, 0.0)] * len(transactions)

    def _prepare_text(self, description: str, merchant_name: Optional[str] = None) -> str:
        """
        Prepare text for ML model.

        Combines description and merchant name, cleans special characters.
        """
        text_parts = [description]

        if merchant_name:
            text_parts.append(merchant_name)

        # Combine and clean
        text = ' '.join(text_parts)
        text = text.lower()

        # Remove special characters but keep spaces
        text = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in text)

        return text

    def get_stats(self) -> Dict:
        """Get model statistics."""
        return {
            **self.stats,
            "model_loaded": self.pipeline is not None,
            "can_predict": self.pipeline is not None and len(self.categories) > 0
        }

    def retrain_from_database(self, db_transactions: List[Dict]) -> Dict:
        """
        Retrain model from database transactions (typically after manual corrections).

        This is called when you want to update the model with latest corrections.
        """
        return self.train_from_transactions(db_transactions)


# Global instance
_categorizer: Optional[TransactionCategorizer] = None


def get_categorizer() -> TransactionCategorizer:
    """Get or create global categorizer instance."""
    global _categorizer
    if _categorizer is None:
        _categorizer = TransactionCategorizer()
    return _categorizer
