"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

export const APPLICATION_TYPE_OPTIONS = [
  "Web Application",
  "Mobile Application",
  "Desktop Application",
  "REST / GraphQL API",
  "Microservices",
  "Backend Service / Worker",
  "Data / ETL Pipeline",
  "Machine Learning / AI",
  "CLI Tool",
  "Library / SDK",
  "Serverless Functions",
  "Embedded / IoT",
];

export const FRONTEND_FRAMEWORK_OPTIONS = [
  "Next.js",
  "React",
  "Angular",
  "Vue.js",
  "Nuxt",
  "Svelte / SvelteKit",
  "Blazor",
  "Razor / MVC Views",
  "React Native",
  "Flutter",
  "HTML / CSS / JavaScript",
  "None (no frontend)",
];

export const BACKEND_FRAMEWORK_OPTIONS = [
  "ASP.NET Core",
  "FastAPI",
  "Django",
  "Flask",
  "Express / Node.js",
  "NestJS",
  "Spring Boot",
  "Ruby on Rails",
  "Laravel",
  "Go (Gin / Fiber)",
  "None (no backend)",
];

export const DATABASE_OPTIONS = [
  "PostgreSQL",
  "MySQL / MariaDB",
  "SQL Server",
  "Oracle",
  "SQLite",
  "MongoDB",
  "Redis",
  "DynamoDB",
  "Cosmos DB",
  "Elasticsearch",
  "None",
];

export const CLOUD_PROVIDER_OPTIONS = [
  "AWS",
  "Microsoft Azure",
  "Google Cloud (GCP)",
  "On-premises",
  "Vercel",
  "Heroku",
  "DigitalOcean",
  "None / undecided",
];

const OTHER = "__other__";

// A dropdown of common choices with an "Other…" entry that reveals a text
// box, so an unlisted technology can still be recorded. The stored value is
// always the plain string (a listed option or the custom text), so it stays
// compatible with everything that already reads these fields.
export function TechStackSelect({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  options: string[];
  placeholder: string;
}) {
  const isKnown = value === "" || options.includes(value);
  const [otherMode, setOtherMode] = useState(!isKnown);
  const selectValue = otherMode ? OTHER : value;

  return (
    <div className="flex flex-col gap-2">
      <Select
        value={selectValue}
        onChange={(e) => {
          if (e.target.value === OTHER) {
            setOtherMode(true);
            onChange("");
          } else {
            setOtherMode(false);
            onChange(e.target.value);
          }
        }}
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
        <option value={OTHER}>Other…</option>
      </Select>
      {otherMode ? (
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Type the name"
          maxLength={100}
          autoFocus
        />
      ) : null}
    </div>
  );
}
