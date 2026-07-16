import re
import statistics
from typing import Dict, Any

class PostQualityEvaluator:
    """Оценивает качество поста по различным метрикам."""
    
    def evaluate(self, text: str) -> float:
        """Возвращает оценку от 0 до 100."""
        scores = []
        scores.append(self._readability(text))
        scores.append(self._engagement(text))
        scores.append(self._view_potential(text))
        scores.append(self._comment_potential(text))
        scores.append(self._click_potential(text))
        scores.append(self._uniqueness(text))
        # Веса
        weights = [0.20, 0.20, 0.15, 0.15, 0.15, 0.15]
        total = sum(s * w for s, w in zip(scores, weights))
        return min(total, 100.0)
    
    def _readability(self, text: str) -> float:
        """Читаемость: длина предложений, количество сложных слов."""
        sentences = re.split(r'[.!?]+', text)
        sentences = [s for s in sentences if len(s.strip()) > 5]
        if not sentences:
            return 0
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        # Идеально 10-15 слов
        if 10 <= avg_len <= 15:
            return 100
        elif 8 <= avg_len <= 20:
            return 70
        else:
            return 40
    
    def _engagement(self, text: str) -> float:
        """Вовлечённость: наличие вопросов, восклицаний, обращений."""
        score = 0
        if '?' in text:
            score += 40
        if '!' in text:
            score += 30
        if 'вы' in text or 'ты' in text or 'вам' in text:
            score += 30
        return min(score, 100)
    
    def _view_potential(self, text: str) -> float:
        """Потенциал просмотров: наличие любопытства, интриги."""
        triggers = ['почему', 'как', 'что будет', 'секрет', 'неожиданно', 'взрыв', 'прорыв', 'шанс']
        score = sum(1 for t in triggers if t in text.lower()) / len(triggers) * 100
        return min(score, 100)
    
    def _comment_potential(self, text: str) -> float:
        """Потенциал комментариев: наличие спорных утверждений, вопросов."""
        score = 0
        if '?' in text:
            score += 50
        if 'согласны' in text.lower() or 'думаете' in text.lower():
            score += 30
        if 'риск' in text.lower() or 'прибыль' in text.lower():
            score += 20
        return min(score, 100)
    
    def _click_potential(self, text: str) -> float:
        """Потенциал перехода по ссылке: наличие CTA, упоминаний выгоды."""
        score = 0
        if 'ссылка' in text.lower() or 'переходи' in text.lower() or 'смотри' in text.lower():
            score += 60
        if 'прибыль' in text.lower() or 'доход' in text.lower():
            score += 40
        return min(score, 100)
    
    def _uniqueness(self, text: str) -> float:
        """Уникальность: оценка повторяющихся фраз."""
        words = text.lower().split()
        if len(words) < 10:
            return 50
        unique_ratio = len(set(words)) / len(words)
        return min(unique_ratio * 100, 100)