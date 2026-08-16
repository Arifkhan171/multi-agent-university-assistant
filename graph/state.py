# # # # ═══════════════════════════════════════════════════════════════
# # # #  graph/state.py  —  Conversation State  (unchanged from Phase 2)
# # # # ═══════════════════════════════════════════════════════════════

# # # from typing import List, Any, Annotated
# # # from typing_extensions import TypedDict
# # # from langchain_core.messages import BaseMessage
# # # import operator


# # # class BotState(TypedDict):
# # #     messages:   Annotated[List[BaseMessage], operator.add]
# # #     user_query: str
# # #     intent:     str
# # #     docs:       List[Any]
# # #     context:    str
# # #     answer:     str
# # #     session_id: str


# # # ═══════════════════════════════════════════════════════════════
# # #  graph/state.py  —  Conversation State
# # #  CHANGE FROM PREVIOUS VERSION:
# # #    Added: retrieval_quality field (for CRAG in D4)
# # #    This field carries the document quality grade through the graph.
# # #    Possible values: "good" | "partial" | "poor" | "empty"
# # # ═══════════════════════════════════════════════════════════════

# # from typing import List, Any, Annotated
# # from typing_extensions import TypedDict
# # from langchain_core.messages import BaseMessage
# # import operator


# # class BotState(TypedDict):
# #     messages:           Annotated[List[BaseMessage], operator.add]
# #     user_query:         str
# #     intent:             str
# #     docs:               List[Any]
# #     context:            str
# #     answer:             str
# #     session_id:         str
# #     retrieval_quality:  str   # "good" | "partial" | "poor" | "empty"  ← NEW (D4 CRAG)


# # ═══════════════════════════════════════════════════════════════
# #  graph/state.py  —  Phase 5  (D5: Summarization Memory)
# # ═══════════════════════════════════════════════════════════════
# #
# #  CHANGE FROM PHASE 4:
# #  Added: summary field
# #  WHY:   combined_node needs to read the session summary and
# #         inject it into the LLM prompt. Passing it through
# #         BotState is the clean LangGraph way to do this.
# #
# # ═══════════════════════════════════════════════════════════════

# from typing import List, Any, Annotated
# from typing_extensions import TypedDict
# from langchain_core.messages import BaseMessage
# import operator


# class BotState(TypedDict):
#     messages:           Annotated[List[BaseMessage], operator.add]
#     user_query:         str
#     intent:             str
#     docs:               List[Any]
#     context:            str
#     answer:             str
#     session_id:         str
#     retrieval_quality:  str   # "good" | "partial" | "poor" | "empty"
#     summary:            str   # compressed memory of earlier conversation (NEW D5)

# ═══════════════════════════════════════════════════════════════
#  graph/state.py  —  Phase 6  (D6: Multi-Agent)
# ═══════════════════════════════════════════════════════════════
#
#  CHANGE FROM PHASE 5:
#  Added: agent_name field
#
#  WHY: The supervisor node sets agent_name after classifying
#  the question. The graph uses it to route to the right worker.
#  Also useful for logging — you can see which agent answered.
#
#  Possible values:
#    "policy"     → Policy Agent
#    "faculty"    → Faculty & Contacts Agent
#    "admissions" → Admissions & Programs Agent
#    "campus"     → Campus Services Agent
#    "general"    → General Agent (catch-all)
#
# ═══════════════════════════════════════════════════════════════

from typing import List, Any, Annotated
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
import operator


class BotState(TypedDict):
    messages:           Annotated[List[BaseMessage], operator.add]
    user_query:         str
    intent:             str
    docs:               List[Any]
    context:            str
    answer:             str
    session_id:         str
    retrieval_quality:  str   # "good" | "partial" | "poor" | "empty"
    summary:            str   # compressed memory of earlier conversation
    agent_name:         str   # which worker agent handled this query (NEW D6)