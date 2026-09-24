def lexical_faithfulness(answer: str, contexts: list[str]) -> float:
    answer_terms = {word.lower() for word in answer.split() if len(word) > 3}
    context = " ".join(contexts).lower()
    return sum(term in context for term in answer_terms) / max(len(answer_terms), 1)
