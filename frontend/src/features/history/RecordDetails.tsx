function humanLabel(value: string) {
  return value.replace(/[_ .]+/g, " ").replace(/^./, (character) => character.toUpperCase());
}

export function RecordDetails({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span>Empty</span>;
  if (Array.isArray(value)) {
    return <ul>{value.map((item, index) => <li key={index}><RecordDetails value={item} /></li>)}</ul>;
  }
  if (typeof value === "object") {
    return (
      <dl>
        {Object.entries(value as Record<string, unknown>)
          .filter(([key]) => !key.endsWith("_id") && !key.includes("file_key") && !key.includes("hash"))
          .map(([key, item]) => (
            <div key={key}><dt>{humanLabel(key)}</dt><dd><RecordDetails value={item} /></dd></div>
          ))}
      </dl>
    );
  }
  return <span>{String(value)}</span>;
}

export { humanLabel };
