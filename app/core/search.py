"""
Optimized search structures for autocomplete suggestions.

This module provides efficient data structures for string searching
and prefix matching, including a trie implementation for O(m) lookup
where m is the length of the search string.
"""
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TrieNode:
    """A node in the trie data structure."""
    children: dict[str, TrieNode] = field(default_factory=dict)
    is_end_of_word: bool = False
    word: str | None = None
    # Store additional metadata for each word
    metadata: dict[str, Any] = field(default_factory=dict)


class Trie:
    """
    Trie (prefix tree) data structure for efficient prefix-based search.

    Time Complexity:
    - Insert: O(m) where m is the length of the word
    - Search: O(m) where m is the length of the search string
    - Prefix search: O(m + n) where m is prefix length and n is number of matches

    Space Complexity: O(alphabet_size * average_word_length * number_of_words)

    This is much more efficient than linear search O(n * m) for prefix matching
    when dealing with large datasets.
    """

    def __init__(self):
        """Initialize an empty trie."""
        self.root = TrieNode()
        self._size = 0

    def insert(self, word: str, metadata: dict[str, Any] | None = None) -> None:
        """
        Insert a word into the trie.

        Args:
            word: The word to insert
            metadata: Optional metadata to associate with the word
        """
        if not word:
            return

        node = self.root
        word_lower = word.lower()

        for char in word_lower:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]

        if not node.is_end_of_word:
            self._size += 1

        node.is_end_of_word = True
        node.word = word  # Store original case
        if metadata:
            node.metadata = metadata

    def search(self, word: str) -> bool:
        """
        Check if an exact word exists in the trie.

        Args:
            word: The word to search for

        Returns:
            True if the exact word exists, False otherwise
        """
        node = self._find_node(word.lower())
        return node is not None and node.is_end_of_word

    def starts_with(self, prefix: str) -> bool:
        """
        Check if any word in the trie starts with the given prefix.

        Args:
            prefix: The prefix to search for

        Returns:
            True if any word starts with the prefix, False otherwise
        """
        return self._find_node(prefix.lower()) is not None

    def find_all_with_prefix(self, prefix: str, limit: int = 100) -> list[str]:
        """
        Find all words that start with the given prefix.

        Args:
            prefix: The prefix to search for
            limit: Maximum number of results to return

        Returns:
            List of words starting with the prefix
        """
        results: list[str] = []
        node = self._find_node(prefix.lower())

        if node is None:
            return results

        self._collect_words(node, results, limit)
        return results

    def find_all_with_prefix_and_metadata(
        self,
        prefix: str,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        Find all words with their metadata that start with the given prefix.

        Args:
            prefix: The prefix to search for
            limit: Maximum number of results to return

        Returns:
            List of dicts containing word and metadata
        """
        results: list[dict[str, Any]] = []
        node = self._find_node(prefix.lower())

        if node is None:
            return results

        self._collect_words_with_metadata(node, results, limit)
        return results

    def find_containing(self, substring: str, limit: int = 100) -> list[str]:
        """
        Find all words that contain the given substring.

        Note: This is O(n * m) where n is total words and m is word length.
        Use prefix search when possible for better performance.

        Args:
            substring: The substring to search for
            limit: Maximum number of results to return

        Returns:
            List of words containing the substring
        """
        results: list[str] = []
        substring_lower = substring.lower()

        for word in self.get_all_words():
            if substring_lower in word.lower():
                results.append(word)
                if len(results) >= limit:
                    break

        return results

    def get_all_words(self) -> Iterator[str]:
        """
        Get all words stored in the trie.

        Yields:
            Each word in the trie
        """
        stack: list[TrieNode] = [self.root]

        while stack:
            node = stack.pop()
            if node.is_end_of_word and node.word:
                yield node.word
            stack.extend(node.children.values())

    def _find_node(self, prefix: str) -> TrieNode | None:
        """
        Find the node corresponding to the given prefix.

        Args:
            prefix: The prefix to find (should be lowercase)

        Returns:
            The node if found, None otherwise
        """
        node = self.root

        for char in prefix:
            if char not in node.children:
                return None
            node = node.children[char]

        return node

    def _collect_words(
        self,
        node: TrieNode,
        results: list[str],
        limit: int
    ) -> None:
        """
        Recursively collect all words from a node.

        Args:
            node: Starting node
            results: List to append results to
            limit: Maximum number of results
        """
        if len(results) >= limit:
            return

        if node.is_end_of_word and node.word:
            results.append(node.word)

        for child in node.children.values():
            if len(results) >= limit:
                return
            self._collect_words(child, results, limit)

    def _collect_words_with_metadata(
        self,
        node: TrieNode,
        results: list[dict[str, Any]],
        limit: int
    ) -> None:
        """
        Recursively collect all words with metadata from a node.

        Args:
            node: Starting node
            results: List to append results to
            limit: Maximum number of results
        """
        if len(results) >= limit:
            return

        if node.is_end_of_word and node.word:
            results.append({
                "word": node.word,
                "metadata": node.metadata
            })

        for child in node.children.values():
            if len(results) >= limit:
                return
            self._collect_words_with_metadata(child, results, limit)

    def __len__(self) -> int:
        """Return the number of words in the trie."""
        return self._size

    def __contains__(self, word: str) -> bool:
        """Check if a word is in the trie."""
        return self.search(word)


class SuggestionIndex:
    """
    Index for autocomplete suggestions with efficient prefix search.

    This class wraps a trie and provides a convenient interface for
    managing autocomplete suggestions with categories and metadata.
    """

    def __init__(self):
        """Initialize the suggestion index."""
        self._trie = Trie()
        self._categories: dict[str, set[str]] = {}

    def add_suggestion(
        self,
        suggestion: str,
        category: str | None = None,
        metadata: dict[str, Any] | None = None
    ) -> None:
        """
        Add a suggestion to the index.

        Args:
            suggestion: The suggestion text
            category: Optional category for the suggestion
            metadata: Optional metadata for the suggestion
        """
        meta = metadata or {}
        if category:
            meta["category"] = category

            # Track suggestions by category
            if category not in self._categories:
                self._categories[category] = set()
            self._categories[category].add(suggestion.lower())

        self._trie.insert(suggestion, meta)

    def add_suggestions_batch(
        self,
        suggestions: list[str],
        category: str | None = None
    ) -> None:
        """
        Add multiple suggestions to the index.

        Args:
            suggestions: List of suggestion texts
            category: Optional category for all suggestions
        """
        for suggestion in suggestions:
            self.add_suggestion(suggestion, category)

    def search_prefix(self, prefix: str, limit: int = 100) -> list[str]:
        """
        Search for suggestions starting with the given prefix.

        Args:
            prefix: The prefix to search for
            limit: Maximum number of results

        Returns:
            List of matching suggestions
        """
        return self._trie.find_all_with_prefix(prefix, limit)

    def search_prefix_with_metadata(
        self,
        prefix: str,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        Search for suggestions with metadata starting with the given prefix.

        Args:
            prefix: The prefix to search for
            limit: Maximum number of results

        Returns:
            List of dicts with suggestion and metadata
        """
        return self._trie.find_all_with_prefix_and_metadata(prefix, limit)

    def search_containing(self, substring: str, limit: int = 100) -> list[str]:
        """
        Search for suggestions containing the given substring.

        Note: Less efficient than prefix search. Use prefix search when possible.

        Args:
            substring: The substring to search for
            limit: Maximum number of results

        Returns:
            List of matching suggestions
        """
        return self._trie.find_containing(substring, limit)

    def search_in_category(
        self,
        prefix: str,
        category: str,
        limit: int = 100
    ) -> list[str]:
        """
        Search for suggestions in a specific category.

        Args:
            prefix: The prefix to search for
            category: The category to search within
            limit: Maximum number of results

        Returns:
            List of matching suggestions in the category
        """
        if category not in self._categories:
            return []

        results = self._trie.find_all_with_prefix_and_metadata(prefix, limit * 2)
        filtered = [
            r["word"] for r in results
            if r.get("metadata", {}).get("category") == category
        ]
        return filtered[:limit]

    def get_categories(self) -> list[str]:
        """
        Get all categories in the index.

        Returns:
            List of category names
        """
        return list(self._categories.keys())

    def get_suggestions_in_category(self, category: str) -> set[str]:
        """
        Get all suggestions in a category.

        Args:
            category: The category name

        Returns:
            Set of suggestions in the category
        """
        return self._categories.get(category, set()).copy()

    def clear(self) -> None:
        """Clear all suggestions from the index."""
        self._trie = Trie()
        self._categories = {}

    def __len__(self) -> int:
        """Return the number of suggestions in the index."""
        return len(self._trie)


# Module-level singleton for convenience
_suggestion_index: SuggestionIndex | None = None


def get_suggestion_index() -> SuggestionIndex:
    """
    Get the global suggestion index instance.

    Returns:
        SuggestionIndex: The global suggestion index
    """
    global _suggestion_index
    if _suggestion_index is None:
        _suggestion_index = SuggestionIndex()
    return _suggestion_index


def reset_suggestion_index() -> None:
    """Reset the global suggestion index."""
    global _suggestion_index
    _suggestion_index = None
