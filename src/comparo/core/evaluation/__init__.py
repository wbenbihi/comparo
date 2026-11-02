"""Small shared mechanics for independent pure evaluation engines."""

from comparo.core.evaluation.fact_ids import derive_fact_id
from comparo.core.evaluation.value_semantics import collection_values
from comparo.core.evaluation.value_semantics import numeric_value
from comparo.core.evaluation.value_semantics import semantic_compare
from comparo.core.evaluation.value_semantics import semantic_equal
from comparo.core.evaluation.value_semantics import text_value

__all__ = (
    "collection_values",
    "derive_fact_id",
    "numeric_value",
    "semantic_compare",
    "semantic_equal",
    "text_value",
)
