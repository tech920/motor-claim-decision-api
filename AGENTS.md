# AI Agents & Models

This repository utilizes local Large Language Models (LLMs) via **Ollama** to act as intelligent agents for processing motor insurance claims. These agents are responsible for understanding accident descriptions, applying complex business rules, and making coverage decisions.

## 🤖 Agent Roles

The system employs two primary agent roles:

### 1. Decision Agent
The core agent responsible for the final claim decision.

*   **Primary Model**: `qwen2.5:14b`
    *   **Why**: Selected for its superior performance with Arabic text and strong reasoning capabilities. It handles the nuances of accident descriptions effectively without requiring prior translation.
*   **Alternative Models**:
    *   `gpt-oss:latest`: Larger model, very capable.
    *   `llama3.1:latest`: Good balance of speed and performance.
    *   `llama3:8b`: Faster, suitable for simpler cases.
*   **Responsibilities**:
    *   Analyzing accident data (JSON/XML).
    *   Applying liability rules (e.g., 100% liability = Rejected).
    *   Evaluating rejection and recovery conditions.
    *   Producing structured JSON decisions (`ACCEPTED`, `REJECTED`, `ACCEPTED_WITH_RECOVERY`).

### 2. Translation Agent
A specialized agent for language localization.

*   **Primary Model**: `llama3.2:latest`
    *   **Why**: Extremely fast and efficient for translation tasks.
*   **Responsibilities**:
    *   Translating Arabic accident descriptions to English (if the Decision Agent requires English input).
    *   Standardizing terminology (e.g., "مسؤولية" -> "Liability").

## ⚙️ Configuration

### Model Selection
Models are configured in the `ClaimProcessor` initialization and can be adjusted in the codebase or via arguments.

```python
processor = ClaimProcessor(
    model_name="qwen2.5:14b",        # Decision Agent
    translation_model="llama3.2:latest" # Translation Agent
)
```

### Business Rules (Agent Prompts)
The "brain" of the agents—the rules and prompts—is decoupled from the code and stored in configuration files:

*   **TP Rules**: `MotorclaimdecisionlinuxTP/claim_config.json`
*   **CO Rules**: `MotorclaimdecisionlinuxCO/claim_config.json`

These files contain:
*   `main_prompt`: The core instruction set for the Decision Agent.
*   `compact_prompt_template`: Optimized prompt for faster processing.
*   `translation_prompt`: Instructions for the Translation Agent.

## 🚀 Setup Requirements

To run these agents, you must have **Ollama** installed and the models pulled:

```bash
# Install Ollama (Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Start Ollama
ollama serve

# Pull required models
ollama pull qwen2.5:14b
ollama pull llama3.2:latest
```

## 🔄 Workflow

1.  **Input**: The system receives claim data (XML/JSON).
2.  **Translation (Optional)**: If enabled, the Translation Agent converts text to English.
3.  **Context Construction**: The system builds a prompt combining the claim data and business rules from `claim_config.json`.
4.  **Decision**: The Decision Agent processes the prompt and returns a JSON object.
5.  **Output**: The system parses the JSON decision and returns it via the API.
