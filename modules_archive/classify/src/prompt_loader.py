import pathlib
from dataclasses import dataclass
from typing import Dict, Any
import yaml

@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    description: str
    system_instruction: str
    user_prompt_template: str

    def render(self, title: str, summary: str) -> str:
        """
        Renders the user prompt template by substituting {title} and {summary}.
        Avoids formatting issues if the title or summary contains curly braces by using raw replacement.
        Unescapes standard double curly braces used in python formatting contracts.
        """
        rendered = self.user_prompt_template.replace("{title}", title).replace("{summary}", summary)
        rendered = rendered.replace("{{", "{").replace("}}", "}")
        return rendered

def load_prompt_templates(prompt_templates_path: pathlib.Path) -> Dict[str, PromptTemplate]:
    """
    Loads all prompt templates from prompt_templates.yaml.
    """
    with open(prompt_templates_path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}
    
    templates_dict = raw_data.get("templates", {})
    templates = {}
    for name, data in templates_dict.items():
        if isinstance(data, dict):
            templates[name] = PromptTemplate(
                name=name,
                version=data.get("version", ""),
                description=data.get("description", ""),
                system_instruction=data.get("system_instruction", "").strip(),
                user_prompt_template=data.get("user_prompt_template", "").strip()
            )
    return templates
