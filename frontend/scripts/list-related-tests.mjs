// Discover transitive Vitest importers without running test bodies.
import { createVitest } from 'vitest/node';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';

const [output, ...inputs] = process.argv.slice(2);
if (!output || !inputs.length) throw new Error('Output and changed sources are required');
const vitest = await createVitest('test', {
  watch: false,
  run: true,
  related: inputs.map((input) => path.resolve(input)),
});
try {
  const tests = new Set();
  const uncovered = [];
  for (const input of inputs) {
    vitest.config.related = [path.resolve(input)];
    const specifications = await vitest.getRelevantTestSpecifications();
    if (!specifications.length) uncovered.push(input);
    for (const spec of specifications) tests.add(spec.moduleId);
  }
  await writeFile(output, JSON.stringify({ tests: [...tests].sort(), uncovered }) + '\n');
} finally {
  await vitest.close();
}
