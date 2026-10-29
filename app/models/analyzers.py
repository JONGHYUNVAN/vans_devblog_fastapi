"""ES 인덱스 공통 설정: 샤드 수와 한국어(Nori)·영어 분석기. ES 서버에 analysis-nori 플러그인이 있어야 한다."""

BASE_INDEX_SETTINGS = {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "max_result_window": 10000,
    "analysis": {
        "analyzer": {
            "korean_analyzer": {
                "type": "custom",
                "tokenizer": "nori_tokenizer",
                "filter": ["lowercase", "nori_part_of_speech", "nori_readingform", "stop"],
            },
            "english_analyzer": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "stop", "snowball"],
            },
            "search_analyzer": {
                "type": "custom",
                "tokenizer": "keyword",
                "filter": ["lowercase"],
            },
        },
        "tokenizer": {
            "nori_tokenizer": {
                "type": "nori_tokenizer",
                "decompound_mode": "mixed",
                # 형태소로 쪼개지 않을 기술 용어
                "user_dictionary_rules": ["Django", "Elasticsearch", "REST API", "Spring Boot", "React", "Vue.js"],
            }
        },
        "filter": {
            "nori_part_of_speech": {
                "type": "nori_part_of_speech",
                # 색인에서 뺄 품사: 어미, 감탄사, 조사, 부사, 관형사, 기호, 접사, 분석 불가 등
                "stoptags": [
                    "E",
                    "IC",
                    "J",
                    "MAG",
                    "MAJ",
                    "MM",
                    "SP",
                    "SSC",
                    "SSO",
                    "SC",
                    "SE",
                    "XPN",
                    "XSA",
                    "XSN",
                    "XSV",
                    "UNA",
                    "NA",
                    "VSV",
                ],
            },
            "nori_readingform": {"type": "nori_readingform"},
            "stop": {"type": "stop", "stopwords": ["_korean_", "_english_"]},
        },
    },
}
