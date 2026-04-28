import uuid
import numpy as np
import valkey

class SemanticCache:
    """
    Python-Side Semantic Cache using standard Valkey Hashes.
    Bypasses the need for server-side FT.SEARCH modules while keeping storage persistent.
    """
    def __init__(self, host="localhost", port=6379, db=1, dim=1536):
        try:
            # Connect to Database 1 so it stays isolated from chat sessions in db=0
            self.client = valkey.Valkey(host=host, port=port, db=db, decode_responses=False)
            self.client.ping()
            print("✅ Semantic Cache connected to db=1 (Python-Side Vector Mode)")
        except Exception as e:
            print(f"⚠️ Semantic cache initialization failed: {e}")
            self.client = None

    def check_cache(self, query_vector, similarity_threshold=0.90):
        """
        Fetches cached vectors from Valkey and compares them to the incoming
        question in one vectorised numpy operation.

        WHY THIS IS NOT A LOOP OF hgetall CALLS ANY MORE: the original version
        ran KEYS, then issued one hgetall per key and computed one dot product
        per key in Python. Both halves scale badly, and a cache lookup sits on
        the critical path of EVERY question:

          * KEYS walks the entire keyspace and BLOCKS the server while it does.
          * one hgetall per key means N sequential network round-trips. At a few
            thousand cached questions that is slower than simply answering the
            question, so the cache would start COSTING latency instead of saving
            it — and it would degrade silently, looking like "the bot got slow".

        Now it is SCAN (cursor-based, non-blocking) plus a single pipeline that
        fetches every hash in one round-trip, plus one matrix multiply for all
        similarities at once. Two round-trips total, regardless of cache size.
        """
        if not self.client:
            return None

        try:
            # SCAN instead of KEYS — same result, but it never blocks the server.
            keys = list(self.client.scan_iter(match="cache:*", count=500))
            if not keys:
                return None

            query_arr  = np.array(query_vector, dtype=np.float32)
            query_norm = np.linalg.norm(query_arr)
            if query_norm == 0:
                return None

            # One round-trip for every hash instead of one per key.
            pipe = self.client.pipeline()
            for key in keys:
                pipe.hgetall(key)
            entries = pipe.execute()

            vectors, responses = [], []
            for cached_data in entries:
                if not cached_data or b"vector" not in cached_data or b"response" not in cached_data:
                    continue

                stored_arr = np.frombuffer(cached_data[b"vector"], dtype=np.float32)

                # Dimension guard. Cached vectors outlive a config change: swap
                # EMBEDDING_MODEL and yesterday's 1536-dim rows are still sitting
                # here under a 24-hour TTL. Comparing them to a 3072-dim query
                # raises, the broad except below swallows it, and the cache goes
                # silently dead for a whole day. Skipping them keeps the cache
                # working through a model change instead.
                if stored_arr.shape != query_arr.shape:
                    continue
                if np.linalg.norm(stored_arr) == 0:
                    continue

                vectors.append(stored_arr)
                responses.append(cached_data[b"response"])

            if not vectors:
                return None

            # All similarities in one matrix op rather than a Python loop.
            matrix = np.vstack(vectors)
            sims   = (matrix @ query_arr) / (np.linalg.norm(matrix, axis=1) * query_norm)

            best_index      = int(np.argmax(sims))
            best_similarity = float(sims[best_index])

            # If the best match has a similarity score >= 90%, serve it instantly!
            if best_similarity >= similarity_threshold:
                print(f"⚡ Cache Hit! Similarity: {best_similarity:.4f}")
                return responses[best_index].decode("utf-8")

        except Exception as e:
            print(f"Cache check error: {e}")

        return None

    def save_to_cache(self, user_query, llm_response, query_vector, ttl_seconds=86400):
        """Saves a new question, response, and vector bytes to Valkey with a 24-hour TTL."""
        if not self.client:
            return
            
        try:
            vec_bytes = np.array(query_vector, dtype=np.float32).tobytes()
            key = f"cache:{uuid.uuid4().hex}"
            
            self.client.hset(key, mapping={
                "query": user_query,
                "response": llm_response,
                "vector": vec_bytes
            })
            # Expire after 24 hours so website policy updates don't stay stale forever
            self.client.expire(key, ttl_seconds)
        except Exception as e:
            print(f"Cache save error: {e}")