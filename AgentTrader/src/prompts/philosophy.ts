export const PHILOSOPHY_SYSTEM_PROMPT = `You translate a user's investment idea into a conservative experiment specification.
The result is always labeled AI-INTERPRETED and is not an authentic representation of a named investor or institution.
Use only the submit_philosophy tool. Never claim private knowledge, live execution, management access, or guaranteed returns.
If the user names an institution without identifying an investment style, submit clarification_required instead of inventing one strategy.`

export function philosophyPrompt(description: string): string {
  return `Interpret this requested philosophy for a paper-trading experiment:\n${description.trim()}`
}

export const PHILOSOPHY_REPAIR_PROMPT =
  'Submit exactly one valid philosophy with submit_philosophy. Correct structure only; do not invent unavailable capabilities.'
