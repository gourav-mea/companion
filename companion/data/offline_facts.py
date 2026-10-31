"""A small offline knowledge pack so common factual questions still work
without any network call -- genuinely offline, not just a cache. Keys are
lowercase topic names matched against the question after stripping common
question phrasing (see intents/offline_knowledge_cmd.py).
"""
OFFLINE_FACTS = {
    "mahatma gandhi": ("Mahatma Gandhi was an Indian lawyer and freedom leader who led India's "
                       "independence movement through nonviolent resistance."),
    "gandhi": ("Mahatma Gandhi was an Indian lawyer and freedom leader who led India's "
              "independence movement through nonviolent resistance."),
    "albert einstein": "Albert Einstein was a German-born physicist best known for the theory of relativity.",
    "einstein": "Albert Einstein was a German-born physicist best known for the theory of relativity.",
    "isaac newton": ("Isaac Newton was an English mathematician and physicist known for the laws "
                     "of motion and universal gravitation."),
    "newton": ("Isaac Newton was an English mathematician and physicist known for the laws "
              "of motion and universal gravitation."),
    "marie curie": "Marie Curie was a physicist and chemist who conducted pioneering research on radioactivity.",
    "india": "India is a country in South Asia, the world's most populous country and the largest democracy.",
    "taj mahal": "The Taj Mahal is a white marble mausoleum in Agra, India, built by Emperor Shah Jahan.",
    "mount everest": "Mount Everest, in the Himalayas, is Earth's highest mountain above sea level.",
}
