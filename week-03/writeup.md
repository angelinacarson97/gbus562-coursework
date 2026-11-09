# Week 3 Writeup: 

## What I Built
I built `ask.py`, a command-line retrieval-augmented generation (RAG) tool. It reads a folder of HTML and PDF policy documents, splits them into short labeled chunks, stores them in a local Chroma database, retrieves the five chunks closest to a question, and asks Claude (`claude-sonnet-5`) to answer using only those chunks with numbered citations. I first built and tested it on Copperline Manufacturing's documents. To make it work with Larkfield Credit Union's documents, I renamed the folder to `larkfield-docs`, gave each document folder its own Chroma database (`.chroma/copperline-docs/` and `.chroma/larkfield-docs/`) so the two collections never mix, and added a `--save` option that writes the retrieved chunks and the answer to a text file. My seven saved runs are in `week-03/runs/`: `q1-before.txt` and `q1-after.txt` show the overdraft fee question before and after I removed an outdated fee schedule, `q2.txt` through `q5.txt` cover rush card delivery, lost-card liability, refunding a wire fee, and check deposit availability, and `q6.txt` is an out-of-scope mortgage rate question.

## Why This Approach
Chunking method: structure

Makes sense on its own: Yes, because each chunk holds one complete section under its heading. For example, the Q4 chunk from "What a member service representative can approve" says a representative "may approve a refund of a single fee of $30 or less without anyone else's approval," which is a complete rule I can understand without reading the rest of the guide.

Label and source: Both, because every retrieved chunk in my saved runs starts with a `Title:` line (such as "Fee Refund Guide for Member Service Staff (Version 2)"), a `Section:` line with the heading, and a `Source:` line with the filename, and the rank line also repeats the source file.

Short enough: Yes, every chunk fits within the embedding model's input limit. Chroma's default embedding model, all-MiniLM-L6-v2, truncates input after 256 tokens. I loaded that model's own tokenizer, turned off its padding and truncation, and counted the tokens in every stored chunk, including the Title, Section, and Source labels. The longest Larkfield chunk is 168 tokens (the Version 8 overdraft fee section), the longest Copperline chunk is 171 tokens, and no chunk in either index is over 256. `ask.py` limits the body text of each chunk to 120 words to keep this margin.

## Example Run
```text
Question: Can I refund the outgoing international wire transfer fee for a member without approval from a manager?
Docs folder: larkfield-docs
Model: claude-sonnet-5
Top k: 5

RETRIEVED CHUNKS

--- Rank 1 | similarity 0.6200 | source consumer-fee-schedule.html ---
Title: Consumer Fee Schedule (Version 8)
Section: Wire transfers
Source: consumer-fee-schedule.html

- Incoming domestic wire transfer: $10.
- Incoming international wire transfer: $15.
- Outgoing domestic wire transfer: $25.
- Outgoing international wire transfer: $45. The receiving bank or an intermediary bank may deduct its own fees from the amount sent.

--- Rank 2 | similarity 0.5794 | source fee-refund-guide.html ---
Title: Fee Refund Guide for Member Service Staff (Version 2)
Section: Refunds that need a branch manager
Source: fee-refund-guide.html

A refund needs the branch manager's approval when the fee is more than $30, when the member has already had a refund in the last 90 days, or when the member asks for several fees to be refunded at once. A branch manager may approve refunds of up to $150 in total for one member in any 90-day period.

--- Rank 3 | similarity 0.5653 | source fee-refund-guide.html ---
Title: Fee Refund Guide for Member Service Staff (Version 2)
Section: What a member service representative can approve
Source: fee-refund-guide.html

A member service representative may approve a refund of a single fee of $30 or less without anyone else's approval. A representative may approve only one refund for the same member in any 90-day period.

--- Rank 4 | similarity 0.4865 | source fee-refund-guide.html ---
Title: Fee Refund Guide for Member Service Staff (Version 2)
Section: Refunds that need the Member Services Director
Source: fee-refund-guide.html

Refunds of more than $150 in total for one member in any 90-day period need the Member Services Director's approval. So does any refund for a business account.

--- Rank 5 | similarity 0.3997 | source fee-refund-guide.html ---
Title: Fee Refund Guide for Member Service Staff (Version 2)
Section: Recording a refund
Source: fee-refund-guide.html

Every refund must be recorded in the member's account notes with the fee, the amount refunded, the reason, and the name of the person who approved it. Refunds that are not recorded this way may be reversed during the monthly audit.

ANSWER
The outgoing international wire transfer fee is $45 [1]. A member service representative may only approve a refund of a single fee without further approval if it is $30 or less [3]. Since $45 exceeds that limit, it requires branch manager approval [2][3].

Citations:
[1] Consumer Fee Schedule (Version 8) > Wire transfers (consumer-fee-schedule.html)
[2] Fee Refund Guide for Member Service Staff (Version 2) > Refunds that need a branch manager (fee-refund-guide.html)
[3] Fee Refund Guide for Member Service Staff (Version 2) > What a member service representative can approve (fee-refund-guide.html)
```

## Reflection
What was wrong: `consumer-fee-schedule-v7.html`, the superseded Version 7 fee schedule, was still in the searchable folder next to the current Version 8 schedule. In `q1-before.txt`, the Version 8 overdraft section ranked first (similarity 0.7328) with a $29 fee, but the Version 7 section ranked a very close second (0.7204) with a $35 fee, and two of the five retrieved chunks came from Version 7. Claude gave $29, but only because the title said "Version 8," and it still described the old $35 fee as a conflict between versions.

How I fixed it: Moved `consumer-fee-schedule-v7.html` out of `larkfield-docs` into `week-03/larkfield-archive/`, then rebuilt the Larkfield Chroma index from the five remaining files (35 chunks) and checked that no chunk came from Version 7. In `q1-after.txt`, all five retrieved chunks came from the Version 8 schedule, and the answer gave only the $29 fee with extra details such as the three-fee daily limit and no fee when the account is overdrawn by $10 or less.

Prevention: Archive process, meaning that when Larkfield publishes a new version of a policy, the person who publishes it moves the old version into an archive folder that the tool never indexes, and the searchable index is rebuilt right away. Each document could also list its version and status, so a quick check could flag any file marked "superseded" that is still in the searchable folder.

Question 6: Declined, because none of the Larkfield documents mention mortgages. The retrieved chunks were the weakest matches of any run (similarity 0.18 to 0.26): fee schedule chunks about stop payments, checking account monthly fees, and the document information line, plus share certificate chunks about dividend rates and maturity. The share certificate rates (3.05% to 3.75% APY) are savings yields on certificates, not loan rates, so they could not support a mortgage rate, and Claude correctly said the documents do not cover it without giving a number.
