import json
import random
from typing import Literal

import dspy


# ----------------------------
# 1) CONFIGURE DSPy + OLLAMA
# ----------------------------
def setup_lm():
    """
    Adjust the model name to one you have pulled in Ollama.
    Examples:
      - ollama_chat/llama3.2
      - ollama_chat/qwen2.5:14b
      - ollama_chat/mistral
    """
    lm = dspy.LM(
        "ollama_chat/qwen2.5:3b",
        api_base="http://localhost:11434",
        api_key="",  # Ollama local server does not require a real key
        temperature=0.2,
    )
    dspy.configure(lm=lm)

    # DSPy has built-in logging utilities.
    dspy.enable_logging()

    print("=" * 80)
    print("DSPy + Ollama configured")
    print(f"LM: {lm.model}")
    print("=" * 80)


# ----------------------------
# 2) DEFINE THE TASK
# ----------------------------
class TriageTicket(dspy.Signature):
    """
    Classify an inbound support ticket for category, urgency, and whether it
    should be escalated to a human support specialist.
    """
    message: str = dspy.InputField(
        desc="A customer support message containing the issue description"
    )
    category: Literal["billing", "technical", "account", "general"] = dspy.OutputField(
        desc="Which team should own the ticket"
    )
    priority: Literal["low", "medium", "high"] = dspy.OutputField(
        desc="Operational urgency of the issue"
    )
    needs_human_followup: Literal["yes", "no"] = dspy.OutputField(
        desc="Whether a human should actively follow up with the customer"
    )


# ----------------------------
# 3) CHOOSE A MODULE
# ----------------------------
class TriageProgram(dspy.Module):
    def __init__(self):
        super().__init__()
        # ChainOfThought is useful here because the task is a bit more complex.
        self.triage = dspy.ChainOfThought(TriageTicket)

    def forward(self, message: str):
        return self.triage(message=message)


# ----------------------------
# 4) TRAIN / DEV DATA
# ----------------------------
def build_datasets():
    # Keep the examples small and obvious at first.
    raw_examples = [
        {
            "message": "I was billed twice this month and need a refund.",
            "category": "billing",
            "priority": "high",
            "needs_human_followup": "yes",
        },
        {
            "message": "The export button crashes every time I click it.",
            "category": "technical",
            "priority": "high",
            "needs_human_followup": "yes",
        },
        {
            "message": "I forgot my password and cannot get the reset email.",
            "category": "account",
            "priority": "medium",
            "needs_human_followup": "yes",
        },
        {
            "message": "Can you tell me whether your platform supports SSO?",
            "category": "general",
            "priority": "low",
            "needs_human_followup": "no",
        },
        {
            "message": "My invoice PDF is missing from the billing portal.",
            "category": "billing",
            "priority": "medium",
            "needs_human_followup": "yes",
        },
        {
            "message": "The mobile app is slow, but I can still use it.",
            "category": "technical",
            "priority": "medium",
            "needs_human_followup": "no",
        },
        {
            "message": "Please update the email address on my account.",
            "category": "account",
            "priority": "low",
            "needs_human_followup": "no",
        },
        {
            "message": "Do you have onboarding guides for new team members?",
            "category": "general",
            "priority": "low",
            "needs_human_followup": "no",
        },
        {
            "message": "Our team cannot log in at all after this morning's update.",
            "category": "technical",
            "priority": "high",
            "needs_human_followup": "yes",
        },
        {
            "message": "I need a copy of last quarter's receipts for accounting.",
            "category": "billing",
            "priority": "low",
            "needs_human_followup": "no",
        },
        {
            "message": "The account owner left the company and we need admin access changed.",
            "category": "account",
            "priority": "high",
            "needs_human_followup": "yes",
        },
        {
            "message": "Where can I find your API documentation?",
            "category": "general",
            "priority": "low",
            "needs_human_followup": "no",
        },
    ]

    examples = [
        dspy.Example(**row).with_inputs("message")
        for row in raw_examples
    ]

    random.seed(42)
    random.shuffle(examples)

    # Small split for demo purposes.
    trainset = examples[:8]
    devset = examples[8:]

    return trainset, devset


# ----------------------------
# 5) METRIC
# ----------------------------
def triage_metric(example, pred, trace=None):
    """
    Return a score between 0 and 1.
    This gives partial credit, which is helpful for multi-output tasks.
    """
    score = 0.0

    if pred.category == example.category:
        score += 1.0
    if pred.priority == example.priority:
        score += 1.0
    if pred.needs_human_followup == example.needs_human_followup:
        score += 1.0

    return score / 3.0


# ----------------------------
# 6) EVALUATION HELPERS
# ----------------------------
def print_prediction(label, example, pred):
    print("-" * 80)
    print(label)
    print(f"INPUT MESSAGE:\n{example.message}\n")
    print("EXPECTED:")
    print(json.dumps(
        {
            "category": example.category,
            "priority": example.priority,
            "needs_human_followup": example.needs_human_followup,
        },
        indent=2,
    ))
    print("PREDICTED:")
    print(json.dumps(
        {
            "category": pred.category,
            "priority": pred.priority,
            "needs_human_followup": pred.needs_human_followup,
        },
        indent=2,
    ))
    print(f"SCORE: {triage_metric(example, pred):.2f}")


def evaluate_program(program, dataset, title):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

    scores = []
    for i, ex in enumerate(dataset, start=1):
        pred = program(message=ex.message)
        score = triage_metric(ex, pred)
        scores.append(score)
        print_prediction(f"Example {i}", ex, pred)

    avg = sum(scores) / len(scores) if scores else 0.0
    print("\n" + "=" * 80)
    print(f"{title} AVERAGE SCORE: {avg:.3f}")
    print("=" * 80)
    return avg


# ----------------------------
# 7) MAIN FLOW
# ----------------------------
def main():
    setup_lm()

    trainset, devset = build_datasets()

    print("\nTrainset size:", len(trainset))
    print("Devset size:", len(devset))

    # Baseline unoptimized program
    baseline = TriageProgram()

    print("\nRunning baseline program before optimization...")
    baseline_score = evaluate_program(
        baseline,
        devset,
        "BASELINE EVALUATION"
    )

    # Show the most recent LM interactions.
    # DSPy exposes inspect_history for prompt/response debugging.
    print("\nRecent DSPy history after baseline run:")
    dspy.inspect_history(n=3)

    # Optimization
    # MIPROv2 is designed to optimize instructions and few-shot examples jointly.
    print("\nStarting optimization with MIPROv2...")
    optimizer = dspy.MIPROv2(
        metric=triage_metric,
        auto="light",   # cheaper / smaller search for local testing
    )

    optimized = optimizer.compile(
        student=TriageProgram(),
        trainset=trainset,
    )

    print("\nRunning optimized program...")
    optimized_score = evaluate_program(
        optimized,
        devset,
        "OPTIMIZED EVALUATION"
    )

    print("\nRecent DSPy history after optimized run:")
    dspy.inspect_history(n=3)

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Baseline score : {baseline_score:.3f}")
    print(f"Optimized score: {optimized_score:.3f}")
    print(f"Improvement    : {optimized_score - baseline_score:+.3f}")

    # Test on a fresh message not in the train/dev split
    test_message = (
        "Since yesterday none of our staff can sign in, and payroll processing is blocked."
    )
    print("\n" + "=" * 80)
    print("FRESH TEST MESSAGE")
    print("=" * 80)
    print(test_message)

    pred = optimized(message=test_message)
    print("\nOptimized prediction:")
    print(json.dumps(
        {
            "category": pred.category,
            "priority": pred.priority,
            "needs_human_followup": pred.needs_human_followup,
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
