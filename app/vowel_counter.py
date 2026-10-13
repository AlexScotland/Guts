"""Reusable vowel counter.

Counts all vowels (a, e, i, o, u) in a given sentence,
handling both uppercase and lowercase letters.
"""


def count_vowels(sentence: str) -> int:
    """Return the number of vowels in *sentence*.

    Vowels counted: a, e, i, o, u (case-insensitive).

    Args:
        sentence: Any string to inspect.

    Returns:
        The total number of vowel characters found.
    """
    vowels = set("aeiouAEIOU")
    return sum(1 for ch in sentence if ch in vowels)


if __name__ == "__main__":
    sample = "Hello, World! How are you today?"
    print(f"Sentence: \"{sample}\"")
    print(f"Vowel count: {count_vowels(sample)}")

    # A few more examples to demonstrate reusability
    for s in ["AEIOU", "rhythm", "The Quick Brown Fox Jumps Over the Lazy Dog"]:
        print(f"  \"{s}\" -> {count_vowels(s)}")
