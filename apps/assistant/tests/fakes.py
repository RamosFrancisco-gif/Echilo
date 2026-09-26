"""Dublês do SDK da Groq: os testes nunca falou com a rede (§8 do steering)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeFunction:
    """Argumentos de uma chamada de ferramenta, como o SDK os entrega."""

    name: str
    arguments: str = "{}"


@dataclass
class FakeToolCall:
    """Chamada de ferramenta devolvida pelo modelo."""

    id: str
    function: FakeFunction


@dataclass
class FakeMessage:
    """Mensagem do modelo, com ou sem chamadas de ferramentas."""

    content: str | None = None
    tool_calls: list[FakeToolCall] = field(default_factory=list)

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        """Imita o `model_dump` do pydantic usado pelo SDK."""
        payload: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in self.tool_calls
            ]
        return payload


@dataclass
class FakeChoice:
    """Escolha devolvida pela completion."""

    message: FakeMessage


@dataclass
class FakeCompletion:
    """Resposta mínima com a forma que `provider.complete` consome."""

    choices: list[FakeChoice]


class FakeCompletions:
    """Reproduz a sequência de respostas programada num `FakeGroqClient`."""

    def __init__(self, script: list[FakeCompletion], calls: list[dict[str, Any]]) -> None:
        self._script = list(script)
        self._calls = calls

    def create(self, **kwargs: Any) -> FakeCompletion:
        """Devolve a próxima resposta do guião e regista a chamada."""
        self._calls.append(kwargs)
        if not self._script:
            raise AssertionError("O modelo foi chamado mais vezes do que o guião previa.")
        return self._script.pop(0)


class FakeGroqClient:
    """Cliente da Groq sem rede, para testes de `GroqProvider` e `ask`."""

    def __init__(self, script: list[FakeCompletion]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.chat = type(
            "FakeChat", (), {"completions": FakeCompletions(script, self.calls)}
        )()

    @property
    def request_count(self) -> int:
        """Número de chamadas feitas ao modelo."""
        return len(self.calls)


def text_reply(content: str) -> FakeCompletion:
    """Resposta simples, sem ferramentas."""
    return FakeCompletion(choices=[FakeChoice(message=FakeMessage(content=content))])


def tool_reply(call_id: str, name: str, arguments: str = "{}") -> FakeCompletion:
    """Resposta que pede uma chamada de ferramenta."""
    return FakeCompletion(
        choices=[
            FakeChoice(
                message=FakeMessage(
                    tool_calls=[FakeToolCall(id=call_id, function=FakeFunction(name, arguments))]
                )
            )
        ]
    )
