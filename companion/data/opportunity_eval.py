"""A small labeled set of example turns for measuring the opportunity
planner's real precision/recall before trusting it live -- run via
`talk.py eval-opportunities`, not part of the regular (fast, offline)
selftest since it needs a reachable Ollama and the heavier model.
"""
OPPORTUNITY_EVAL_SET = [
    ("My friend Raj's birthday is on December 25th.", True),
    ("I'm planning a trip to Manali.", True),
    ("We have a dentist appointment next week.", True),
    ("My anniversary is coming up next month.", True),
    ("What's the weather like today?", False),
    ("Set a timer for five minutes.", False),
    ("What is twelve times seven?", False),
    ("I might go to Manali someday, who knows.", False),
    ("Tell me a joke.", False),
    ("What's on my shopping list?", False),
]
