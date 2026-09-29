from pathlib import Path

from proposal_writer import generate_proposal


transcript_path = Path("test_transcript.txt")

transcript_text = transcript_path.read_text(
    encoding="utf-8"
)

output = generate_proposal(
    transcript_text,
    client_name="Test Client"
)

print()
print("====================================")
print("PROPOSAL GENERATED")
print("====================================")
print(output)