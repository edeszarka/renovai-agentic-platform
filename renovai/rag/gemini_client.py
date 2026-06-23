import re
import logging
from google import genai
from google.genai import types
from pydantic import BaseModel
from typing import List, Optional, Literal, Dict, Any
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

class GeminiConfig(BaseModel):
    model_name: str = "gemini-flash-latest" # Prioritize 'latest' alias for stability
    temperature: float = 0.2
    max_output_tokens: int = 4096       # Increased from 2048 to prevent truncation
    api_key: str

class RAGResponse(BaseModel):
    answer: str                    # Hungarian answer text
    sources_cited: List[str]       # list of source_file values actually used
    confidence: Literal["high", "medium", "low"]
    retrieval_metadata: dict
    tokens_used: int
    finish_reason: Optional[str] = None # Added for debugging


SYSTEM_PROMPT = """
Te egy tapasztalt magyar ingatlan- és felújítási tanácsadó AI asszisztens vagy.
Kizárólag a megadott KONTEXTUS DOKUMENTUMOK alapján válaszolj.
Ha a válasz nem található meg a forrásokban, azt egyértelműen jelezd.
Mindig magyarul válaszolj.
Válaszaidban hivatkozz a forrásokra szögletes zárójelben: pl. "[FORRÁS 2]".
Légy konkrét, praktikus és tömör.
Ha árakat adsz meg, mindig jelezd, hogy melyik évből és melyik kerületből 
származnak az adatok.
"""

def get_client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)

def _is_retryable_exception(e):
    err_msg = str(e)
    if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
        return False # Don't retry locally, move to fallback
    return True # Retry on other exceptions (500, timeouts, etc.)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(Exception),
    before_sleep=lambda retry_state: logger.warning(
        f"Transient error. Retrying in {retry_state.next_action.sleep}s..."
    )
)
def _generate_with_smart_retry(client: genai.Client, model: str, contents: Any, config: types.GenerateContentConfig):
    try:
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=config
        )
    except Exception as e:
        if not _is_retryable_exception(e):
            # Log and raise to exit tenacity retry loop immediately on quota issues
            logger.info(f"Model {model} hit quota limit. Switching to fallback...")
            raise e
        raise e

def call_gemini(
    query: str,
    context: str,
    config: GeminiConfig,
    retrieval_metadata: dict,
    conversation_history: Optional[List[dict]] = None
) -> RAGResponse:
    """Calls Gemini API with retrieved context and query, with fallback logic."""
    client = get_client(config.api_key)
    
    # Construct prompt
    prompt_text = f"""
KONTEXTUS DOKUMENTUMOK:
{context}

KÉRDÉS: {query}

VÁLASZ:
"""
    
    # Prepare contents for google-genai
    contents = []
    if conversation_history:
        for turn in conversation_history:
            role = "user" if turn["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=turn["content"])]))
    
    contents.append(types.Content(role="user", parts=[types.Part(text=prompt_text)]))
    
    # Ensure system_instruction is a Content object just to be safe
    sys_inst = types.Content(parts=[types.Part(text=SYSTEM_PROMPT)])

    gen_config = types.GenerateContentConfig(
        system_instruction=sys_inst,
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
    )
    
    # Model fallback list - diverse selection to bypass specific model quotas
    models_to_try = [
        config.model_name,
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-flash-latest",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
    ]
    
    # Remove duplicates while preserving order
    models_to_try = list(dict.fromkeys(models_to_try))
    
    last_exception = None
    for base_model_id in models_to_try:
        model_id = base_model_id
        if not (model_id.startswith("models/") or model_id.startswith("tunedModels/")):
            model_id = f"models/{model_id}"
            
        try:
            logger.info(f"Attempting query with model: {model_id}")
            response = _generate_with_smart_retry(
                client=client,
                model=model_id,
                contents=contents,
                config=gen_config
            )
            
            answer_text = response.text
            
            # Diagnostic info
            finish_reason = None
            if response.candidates and len(response.candidates) > 0:
                finish_reason = response.candidates[0].finish_reason
                if finish_reason != "STOP":
                    logger.warning(f"Model {model_id} finished with non-standard reason: {finish_reason}")
                
            if response.usage_metadata:
                logger.info(f"Success with {model_id}. Tokens: {response.usage_metadata.total_token_count}")

            # Extract citations [FORRÁS n]
            citation_numbers = re.findall(r'\[FORRÁS (\d+)\]', answer_text)
            citation_indices = sorted(list(set(int(n) for n in citation_numbers)))
            
            # Confidence logic
            num_cited = len(citation_indices)
            if num_cited >= 4:
                confidence = "high"
            elif num_cited >= 2:
                confidence = "medium"
            else:
                confidence = "low"
                
            return RAGResponse(
                answer=answer_text,
                sources_cited=[], # Will be filled by pipeline using chunk info
                confidence=confidence,
                retrieval_metadata=retrieval_metadata,
                tokens_used=response.usage_metadata.total_token_count if response.usage_metadata else 0,
                finish_reason=finish_reason
            )
        except Exception as e:
            logger.warning(f"Model {model_id} failed or exhausted. Trying next fallback... Error: {e}")
            last_exception = e
            continue
            
    # If all models fail
    logger.error(f"All fallback models failed. Last error: {last_exception}")
    raise last_exception

async def stream_gemini(
    query: str,
    context: str,
    config: GeminiConfig,
    conversation_history: Optional[List[dict]] = None
):
    """Streams Gemini API response."""
    client = get_client(config.api_key)
    
    prompt_text = f"""
KONTEXTUS DOKUMENTUMOK:
{context}

KÉRDÉS: {query}

VÁLASZ:
"""
    
    contents = []
    if conversation_history:
        for turn in conversation_history:
            role = "user" if turn["role"] == "user" else "model"
            contents.append(types.Content(role=role, parts=[types.Part(text=turn["content"])]))
            
    contents.append(types.Content(role="user", parts=[types.Part(text=prompt_text)]))

    gen_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
    )
    
    # Note: Stream doesn't have retry in this simplified async version for now
    response = await client.aio.models.generate_content(
        model=config.model_name,
        contents=contents,
        config=gen_config,
        stream=True
    )
    
    async for chunk in response:
        yield chunk.text
