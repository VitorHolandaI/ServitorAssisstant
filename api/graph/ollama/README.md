# LangChain Ollama — Examples

Source: https://reference.langchain.com/python/integrations/langchain_ollama/#langchain_ollama.ChatOllama  
See full list of supported init args and their descriptions in the params section.

---

## Instantiate

```python
from langchain_ollama import ChatOllama

model = ChatOllama(
    model="gpt-oss:20b",
    validate_model_on_init=True,
    temperature=0.8,
    num_predict=256,
    # other params ...
)
```

---

## Invoke

```python
messages = [
    ("system", "You are a helpful translator. Translate the user sentence to French."),
    ("human", "I love programming."),
]
model.invoke(messages)
```

**Output**

```
AIMessage(
  content="J'adore le programmation. (Note: ... )",
  response_metadata={...},
)
```

---

## Stream

```python
for chunk in model.stream("Return the words Hello World!"):
    print(chunk.text, end="")
```

**Output**

```
Hello
 World
!
```

---

### Stream with Messages

```python
stream = model.stream(messages)
full = next(stream)

for chunk in stream:
    full += chunk

full
```

**Output**

```
AIMessageChunk(
  content="Je adore le programmation. (...note...)",
  response_metadata={...}
)
```

---

## Async Examples

### ainvoke

```python
await model.ainvoke("Hello how are you!")
```

**Output**

```
AIMessage(
  content="Hi there! I'm just an AI...",
  response_metadata={...}
)
```

---

### astream

```python
async for chunk in model.astream("Say hello world!"):
    print(chunk.content)
```

**Output**

```
HEL
LO
WORLD
!
```

---

### abatch

```python
messages = [
    ("human", "Say hello world!"),
    ("human", "Say goodbye world!")
]

await model.abatch(messages)
```

**Output**

```python
[
  AIMessage(
      content="HELLO, WORLD!",
      response_metadata={...}
  ),
  AIMessage(
      content="It's been a blast chatting with you! ...",
      response_metadata={...}
  ),
]
```

---

## JSON Mode

```python
json_model = ChatOllama(format="json")

json_model.invoke(
    "Return a query for the weather in a random location and time of day "
    "with two keys: location and time_of_day. Respond using JSON only."
).content
```

**Output**

```json
{"location": "Pune, India", "time_of_day": "morning"}
```

---

## Tool Calling

```python
from langchain_ollama import ChatOllama
from pydantic import BaseModel, Field

class Multiply(BaseModel):
    a: int = Field(..., description="First integer")
    b: int = Field(..., description="Second integer")

ans = await chat.invoke("What is 45*67")
ans.tool_calls
```

**Output**

```python
[
    {
        "name": "Multiply",
        "args": {"a": 45, "b": 67},
        "id": "420c3f3b-df10-4188-945f-eb3abdb40622",
        "type": "tool_call",
    }
]
```


