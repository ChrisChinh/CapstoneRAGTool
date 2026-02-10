from pydantic import BaseModel
import json
import os

INFO_PATH = "config/index_info.json"


class IndexInfo(BaseModel):
    name: str
    dimensions: int
    created_at: str
    description: str
    status: str


class IndexList:
    def __init__(self):
        self.indexes: list[IndexInfo] = []
        self.load_indexes()
        
    def load_indexes(self):
        try:
            with open(INFO_PATH, "r") as f:
                data = json.load(f)
                self.indexes = [IndexInfo(**idx) for idx in data.get("indexes", [])]
        except FileNotFoundError:
            os.makedirs(os.path.dirname(INFO_PATH), exist_ok=True)
            with open(INFO_PATH, "w") as f:
                json.dump({"indexes": []}, f)
            self.indexes = []


    def get_index_info(self, index_name) -> IndexInfo | None:
        for idx in self.indexes:
            if idx.name == index_name:
                return idx
        return None
    
    def save_index_info(self, index_info: IndexInfo):
        existing = self.get_index_info(index_info.name)
        if existing:
            self.indexes = [idx if idx.name != index_info.name else index_info for idx in self.indexes]
        else:
            self.indexes.append(index_info)
        with open(INFO_PATH, "w") as f:
            json.dump({"indexes": [idx.dict() for idx in self.indexes]}, f, indent=4)


    def delete_index_info(self, index_name):
        self.indexes = [idx for idx in self.indexes if idx.name != index_name]
        with open(INFO_PATH, "w") as f:
            json.dump({"indexes": [idx.dict() for idx in self.indexes]}, f, indent=4)


