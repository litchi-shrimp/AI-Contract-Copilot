#!/usr/bin/env python3
"""
检索模板参考内容

description: 从模板库中检索与用户需求相关的参考内容，使用jieba分词和标准BM25算法
params:
  query: 用户的需求描述或问题
  top_k: 返回的相关结果数量，默认为5
returns: 相关模板内容列表
"""
import sys
import os
import json
import math
from typing import List, Dict, Any, Tuple
import jieba

class BM25:
    """标准BM25算法实现"""
    def __init__(self, documents: List[List[str]]):
        """初始化BM25

        Args:
            documents: 文档集合，每个文档是分词后的词列表
        """
        self.documents = documents
        self.doc_count = len(documents)
        self.avg_doc_len = sum(len(doc) for doc in documents) / self.doc_count if self.doc_count > 0 else 0
        self.idf = self._calculate_idf()
        self.k1 = 1.2
        self.b = 0.75

    def _calculate_idf(self) -> Dict[str, float]:
        """计算每个词的IDF值

        Returns:
            词到IDF值的映射
        """
        df = {}
        for doc in self.documents:
            unique_words = set(doc)
            for word in unique_words:
                df[word] = df.get(word, 0) + 1

        idf = {}
        for word, freq in df.items():
            idf[word] = math.log((self.doc_count - freq + 0.5) / (freq + 0.5) + 1.0)
        return idf

    def score(self, query: List[str], doc_idx: int) -> float:
        """计算查询与文档的BM25分数

        Args:
            query: 查询的分词列表
            doc_idx: 文档索引

        Returns:
            BM25分数
        """
        doc = self.documents[doc_idx]
        doc_len = len(doc)
        score = 0.0

        for word in query:
            if word not in self.idf:
                continue

            tf = doc.count(word) / doc_len
            idf = self.idf[word]
            numerator = idf * tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_len)
            score += numerator / denominator

        return score

class RetrieveTemplateReferenceTool:
    def __init__(self, outline_manager=None):
        """初始化检索工具

        Args:
            outline_manager: OutlineManager实例
        """
        self.outline_manager = outline_manager
        self.match_result = None
        self.section_index = []
        self.bm25 = None
        
        if outline_manager is not None:
            self.match_result = outline_manager.get_match_result()
            self._build_index()
        else:
            self._load_match_result()

    def _load_match_result(self):
        """加载匹配结果文件"""
        try:
            match_result_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                'match_result.json'
            )
            if os.path.exists(match_result_path):
                with open(match_result_path, 'r', encoding='utf-8') as f:
                    self.match_result = json.load(f)
                self._build_index()
            else:
                print(f"匹配结果文件不存在: {match_result_path}")
        except Exception as e:
            print(f"加载匹配结果失败: {e}")

    def _build_index(self):
        """构建索引，将每个一级标题及其下的所有条款内容进行索引"""
        if not self.match_result:
            return

        documents = []
        for template_item in self.match_result:
            template = template_item.get('template', {})
            if '正文章节' in template:
                for section in template['正文章节']:
                    section_text = self._extract_section_text(section)
                    words = self._extract_keywords(section_text)
                    documents.append(words)
                    self.section_index.append({
                        'section': section,
                        'full_text': section_text,
                        'words': words,
                        'template_info': {
                            'template_name': template_item.get('metadata', {}).get('template_name', ''),
                            'contract_type': template_item.get('metadata', {}).get('contract_type', '')
                        }
                    })

        if documents:
            self.bm25 = BM25(documents)

    def _extract_section_text(self, section: Dict[str, Any]) -> str:
        """提取章节的完整文本内容

        Args:
            section: 章节对象

        Returns:
            章节的完整文本内容
        """
        text_parts = []

        section_title = section.get('章节标题', '')
        section_num = section.get('章节编号', '')
        if section_title:
            text_parts.append(f"{section_num} {section_title}")

        if '条款列表' in section:
            for clause in section['条款列表']:
                clause_num = clause.get('条款编号', '')
                clause_desc = clause.get('条款内容', clause.get('条款大致说明', ''))
                if clause_desc:
                    text_parts.append(f"{clause_num} {clause_desc}")

                if '子条款列表' in clause:
                    for sub_clause in clause['子条款列表']:
                        sub_clause_num = sub_clause.get('子条款编号', '')
                        sub_clause_desc = sub_clause.get('子条款内容', sub_clause.get('子条款大致说明', ''))
                        if sub_clause_desc:
                            text_parts.append(f"{sub_clause_num} {sub_clause_desc}")

                        if '子子条款列表' in sub_clause:
                            for sub_sub_clause in sub_clause['子子条款列表']:
                                sub_sub_clause_num = sub_sub_clause.get('子子条款编号', '')
                                sub_sub_clause_desc = sub_sub_clause.get('子子条款内容', sub_sub_clause.get('子子条款大致说明', ''))
                                if sub_sub_clause_desc:
                                    text_parts.append(f"{sub_sub_clause_num} {sub_sub_clause_desc}")

        return ' '.join(text_parts)

    def _extract_keywords(self, text: str) -> List[str]:
        """从文本中提取关键词，使用jieba分词

        Args:
            text: 文本内容

        Returns:
            关键词列表
        """
        stopwords = {
            '的', '了', '和', '是', '在', '有', '与', '或', '等', '及', '为', '以', '对', '将', '把', '被',
            '请', '如', '若', '以及', '及其', '且', '并', '但', '或者', '如果', '当', '时', '应当', '可以',
            '必须', '不得', '这个', '那个', '什么', '如何', '怎么', '为什么', '因为', '所以', '但是', '而且'
        }
        words = list(jieba.cut(text))
        keywords = [w for w in words if len(w) >= 2 and w not in stopwords]
        return keywords

    def _calculate_bm25_score(self, query: str, doc_idx: int) -> float:
        """计算BM25相似度分数

        Args:
            query: 查询文本
            doc_idx: 文档索引

        Returns:
            BM25分数
        """
        if not self.bm25:
            return 0.0

        query_words = self._extract_keywords(query)
        if not query_words:
            return 0.0

        return self.bm25.score(query_words, doc_idx)

    def _format_section(self, section: Dict[str, Any], template_info: Dict[str, str] = None) -> str:
        """格式化章节为可读字符串

        Args:
            section: 章节对象
            template_info: 模板信息

        Returns:
            格式化后的章节字符串
        """
        result = []
        if template_info and template_info.get('template_name'):
            result.append(f"模板: {template_info['template_name']}")
        
        section_num = section.get('章节编号', '')
        section_title = section.get('章节标题', '')
        result.append(f"{section_num} {section_title}")

        if '条款列表' in section:
            for clause in section['条款列表']:
                clause_num = clause.get('条款编号', '')
                clause_desc = clause.get('条款内容', clause.get('条款大致说明', ''))
                result.append(f"  {clause_num} {clause_desc}")

                if '子条款列表' in clause:
                    for sub_clause in clause['子条款列表']:
                        sub_clause_num = sub_clause.get('子条款编号', '')
                        sub_clause_desc = sub_clause.get('子条款内容', sub_clause.get('子条款大致说明', ''))
                        result.append(f"    {sub_clause_num} {sub_clause_desc}")

                        if '子子条款列表' in sub_clause:
                            for sub_sub_clause in sub_clause['子子条款列表']:
                                sub_sub_clause_num = sub_sub_clause.get('子子条款编号', '')
                                sub_sub_clause_desc = sub_sub_clause.get('子子条款内容', sub_sub_clause.get('子子条款大致说明', ''))
                                result.append(f"      {sub_sub_clause_num} {sub_sub_clause_desc}")

        return '\n'.join(result)

    def execute(self, query: str, top_k: int = 3, retry_count: int = 0) -> str:
        """执行检索操作

        Args:
            query: 用户的需求描述或问题
            top_k: 返回的相关结果数量
            retry_count: 当前重试次数

        Returns:
            检索结果
        """
        if not self.section_index or not self.bm25:
            return "未找到匹配的模板参考内容"

        scored_sections = []
        for idx, item in enumerate(self.section_index):
            score = self._calculate_bm25_score(query, idx)
            if score > 0:
                scored_sections.append((idx, score, item['section'], item.get('template_info', {})))

        scored_sections.sort(key=lambda x: x[1], reverse=True)
        top_results = scored_sections[:top_k]

        if not top_results and retry_count == 0:
            return self._generate_retry_response(query, retry_count)
        elif not top_results and retry_count > 0:
            return "经过多次尝试，仍未找到相关的模板参考内容，建议使用Web Search获取更多信息。"

        result_parts = []
        for idx, (section_idx, score, section, template_info) in enumerate(top_results, 1):
            result_parts.append(f"--- 参考结果 {idx} (相关度: {score:.2f}) ---")
            result_parts.append(self._format_section(section, template_info))
            result_parts.append("")

        if retry_count == 0 and not self._is_relevant(query, top_results):
            return self._generate_retry_response(query, retry_count)

        return '\n'.join(result_parts)

    def _is_relevant(self, query: str, top_results: List[Tuple]) -> bool:
        """检查检索结果是否与查询相关

        Args:
            query: 查询文本
            top_results: 检索结果列表

        Returns:
            是否相关
        """
        if not top_results:
            return False

        query_keywords = self._extract_keywords(query)
        query_word_count = len(query_keywords)
        
        for idx, score, section, template_info in top_results:
            section_keywords = self.section_index[idx]['words']
            common_keywords = set(query_keywords) & set(section_keywords)
            
            if query_word_count >= 2 and len(common_keywords) >= 2:
                return True
            elif query_word_count == 1 and len(common_keywords) >= 1:
                return True
            elif len(common_keywords) >= 1:
                return True
                
        return False

    def _generate_retry_response(self, query: str, retry_count: int) -> str:
        """生成重试响应

        Args:
            query: 查询文本
            retry_count: 当前重试次数

        Returns:
            重试响应
        """
        if retry_count == 0:
            return "本次检索未找到相关内容，正在尝试使用不同的关键词..."

        return ""
    
if __name__ == "__main__":
    # 测试检索工具
    retrieve_tool = RetrieveTemplateReferenceTool()
    results = retrieve_tool.execute("租金，租金支付方式")
    print(results)