#!/usr/bin/env python3
"""
Skill管理器，用于动态加载和管理技能
"""
import os
import sys
from pathlib import Path
from typing import Optional, Dict

class SkillManager:
    """Skill管理器，负责动态加载技能和读取SKILL.md文件"""
    
    def __init__(self, skills_dir: Optional[str] = None):
        """初始化Skill管理器
        
        Args:
            skills_dir: 技能目录路径
        """
        self.skills_dir = skills_dir or Path(__file__).parent.parent / "skills"
        self.loaded_skills = {}
    
    def load_skill(self, skill_name: str) -> Optional[str]:
        """加载指定技能的SKILL.md文件内容
        
        Args:
            skill_name: 技能名称
            
        Returns:
            SKILL.md文件内容，如果加载失败返回None
        """
        try:
            skill_path = Path(self.skills_dir) / skill_name
            skill_file = skill_path / "SKILL.md"
            
            if not skill_file.exists():
                return None
            
            with open(skill_file, "r", encoding="utf-8") as f:
                content = f.read()
            
            self.loaded_skills[skill_name] = content
            return content
        except Exception as e:
            print(f"加载技能 {skill_name} 失败: {e}", file=sys.stderr)
            return None
    
    def get_skill_content(self, skill_name: str) -> Optional[str]:
        """获取已加载技能的内容
        
        Args:
            skill_name: 技能名称
            
        Returns:
            技能内容，如果技能未加载返回None
        """
        return self.loaded_skills.get(skill_name)
    
    def list_skills(self) -> list:
        """列出所有可用的技能
        
        Returns:
            技能名称列表
        """
        skills = []
        try:
            for item in os.listdir(self.skills_dir):
                item_path = Path(self.skills_dir) / item
                if item_path.is_dir() and (item_path / "SKILL.md").exists():
                    skills.append(item)
        except Exception as e:
            print(f"列出技能失败: {e}", file=sys.stderr)
        return skills
    
    def get_skill_description(self, skill_name: str) -> Optional[str]:
        """获取技能的描述信息
        
        Args:
            skill_name: 技能名称
            
        Returns:
            技能描述信息，如果加载失败返回None
        """
        content = self.get_skill_content(skill_name)
        if not content:
            content = self.load_skill(skill_name)
        
        if content:
            # 提取技能描述（假设SKILL.md文件开头是描述）
            lines = content.split('\n')
            description = []
            for line in lines:
                if line.strip() and not line.startswith('#'):
                    description.append(line)
                elif line.startswith('#') and len(description) > 0:
                    break
            return '\n'.join(description)
        return None


if __name__ == "__main__":
    skill_manager = SkillManager()
    print(skill_manager.list_skills())
    print(skill_manager.get_skill_description("outline-editor"))
    print(skill_manager.get_skill_content("outline-editor"))