# F3 correlation agent

From the Observa repository root, install the F3 dependencies and run an F2 fixture:

```powershell
python -m pip install -r correlation_agent/requirements.txt
python -m correlation_agent investigation/fixtures/investigation_result_completed.json
```

Set `GROQ_API_KEY` in `correlation_agent/.env` or the process environment.
The agent calls Groq's OpenAI-compatible endpoint using `openai/gpt-oss-120b`.
Each investigation sends the F2 findings, missing evidence and any supplied
historical notes to Groq. The CLI prints the F3 result as JSON.

* `model.py` configures the Groq model and system instructions.
* `graph.py` builds the agent with LangChain's `create_agent` and limits it to five model calls.
* `tools.py` filters evidence, inspects references and validates the chosen handoff.
* `state.py` defines the graph state and the public result.

The model can inspect an F2 finding, submit ranked candidate causes, or request
specific evidence from the F2 agents. Python derives evidence links from cited
observation rows and checks that a final cause has two distinct signals on the
same service and snapshot. These checks establish citation validity; the causal
interpretation is the model's judgment. A follow-up request is returned for the
caller to dispatch. Groq failures surface as errors.

Python callers can use `from correlation_agent import correlate` and pass an F2
`InvestigationResult` or its dictionary. The optional `memory_lookup(incident_id,
services)` callback supplies historical notes.
