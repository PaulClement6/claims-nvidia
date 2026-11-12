---
name: ste
description: Write in Simplified Technical English. Use for every explanation, summary, plan, question or status message written to the user.
---

# Simplified Technical English

Write so a non-native speaker understands each sentence on the first read.
These rules are based on ASD-STE100 (Simplified Technical English), adapted for chat.

## Sentences

- One idea per sentence.
- Instructions: 20 words or fewer. Descriptions: 25 words or fewer.
- Use the active voice. Say who does the action.
- Use simple tenses: present, past, future.
- For steps, use a numbered list with one action per step.
- Start an instruction with the verb: "Run the tests." Not "You may want to run the tests."

## Words

- Use common words with one clear meaning.
- Use the same word for the same thing every time. Do not use synonyms for variety.
- Define a technical term the first time you use it, in one short sentence.
- Do not use metaphors, idioms or vague jargon. Banned examples:
  lever, knob, surface (as a verb), load-bearing, counterfactual, under the hood,
  move the needle, unlock, leverage, robust, seamless.
- Say the exact thing. Not "some issues". Say "2 tests fail in `auth.test.ts`".

## Structure

- Start with the answer or result. Then give the details.
- Short paragraphs: 3 sentences or fewer.
- When you are not sure, say so in one sentence. Do not hedge every sentence.

## Example

Bad: "The auth layer is somewhat load-bearing here, so we'd want to be careful about how we surface errors."

Good: "Many parts of the app use the login code. If we change how it reports errors, those parts can break. I will add tests first."
