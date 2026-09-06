import { createHash } from 'node:crypto';

export function normalizeHttpUrl(input) {
  const url = new URL(input);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Only HTTP(S) resources are supported');
  url.hash = '';
  url.protocol = url.protocol.toLowerCase();
  url.hostname = url.hostname.toLowerCase();
  if ((url.protocol === 'http:' && url.port === '80') || (url.protocol === 'https:' && url.port === '443')) url.port = '';
  return url.toString();
}

export function resourceIdForUrl(input) {
  return `res_${createHash('sha256').update(normalizeHttpUrl(input)).digest('hex').slice(0, 24)}`;
}

export function stableHash(value) {
  return createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value)).digest('hex');
}
