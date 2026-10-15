import { withRetry } from "@/lib/rag/gemini";

/** The slice of @google/genai used for text generation; tests pass fakes with the same shape. */
export type GenClient = {
  models: {
    generateContent(req: { model: string; contents: string; config?: Record<string, unknown> }): Promise<{ text?: string }>;
    generateContentStream(req: { model: string; contents: string; config?: Record<string, unknown> }): Promise<AsyncIterable<{ text?: string }>>;
  };
};

export async function generateJson<T>(client: GenClient, model: string, prompt: string, schema: object): Promise<T> {
  const response = await withRetry(() =>
    client.models.generateContent({ model, contents: prompt, config: { temperature: 0, responseMimeType: "application/json", responseSchema: schema } }),
  );
  if (!response.text?.trim()) throw new Error(`${model} returned no text`);
  return JSON.parse(response.text) as T;
}

export async function generateText(client: GenClient, model: string, prompt: string): Promise<string> {
  const response = await withRetry(() => client.models.generateContent({ model, contents: prompt, config: { temperature: 0 } }));
  if (!response.text?.trim()) throw new Error(`${model} returned no text`);
  return response.text.trim();
}

export async function* streamText(client: GenClient, model: string, system: string, prompt: string): AsyncGenerator<string> {
  const stream = await withRetry(() =>
    client.models.generateContentStream({ model, contents: prompt, config: { systemInstruction: system, temperature: 0.1 } }),
  );
  for await (const chunk of stream) {
    if (chunk.text) yield chunk.text;
  }
}
