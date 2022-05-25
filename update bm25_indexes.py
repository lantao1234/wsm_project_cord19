import numpy as np # linear algebra
import pandas as pd # data processing, CSV file I/O (e.g. pd.read_csv)
from pathlib import Path, PurePath
import pandas as pd
import requests
from requests.exceptions import HTTPError, ConnectionError
from rank_bm25 import BM25Okapi
import nltk
from nltk.corpus import stopwords
# nltk.download("punkt")
import re
import pandas as pd

"""只需在构架索引时运行一次即可，基于BM25的index"""

'''
FILES PATH
'''
input_dir = PurePath('2020-04-03')

# The all sources metadata file
metadata = pd.read_csv(input_dir / 'metadata.csv',
                       dtype={'Microsoft Academic Paper ID': str,
                             'pubmed_id': str})

# Convert the doi to a url
def doi_url(d): return f'http://{d}' if d.startswith('doi.org') else f'http://doi.org/{d}' 
metadata.doi = metadata.doi.fillna('').apply(doi_url)

# Set the abstract to the paper title if it is null
metadata.abstract = metadata.abstract.fillna(metadata.title)

# Some papers are duplicated since they were collected from separate sources. Thanks Joerg Rings
duplicate_paper = ~(metadata.title.isnull() | metadata.abstract.isnull()) & (metadata.duplicated(subset=['title', 'abstract']))
metadata = metadata[~duplicate_paper].reset_index(drop=True)

def get(url, timeout=6):
    try:
        r = requests.get(url, timeout=timeout)
        return r.text
    except ConnectionError:
        print(f'Cannot connect to {url}')
        print(f'Remember to turn Internet ON in the Kaggle notebook settings')
    except HTTPError:
        print('Got http error', r.status, r.text)

class DataHolder:
    '''
    A wrapper for a dataframe with useful functions for notebooks
    '''
    def __init__(self, data: pd.DataFrame):
        self.data = data
        
    def __len__(self): return len(self.data)
    def __getitem__(self, item): return self.data.loc[item]
    def head(self, n:int): return DataHolder(self.data.head(n).copy())
    def tail(self, n:int): return DataHolder(self.data.tail(n).copy())
    def _repr_html_(self): return self.data._repr_html_()
    def __repr__(self): return self.data.__repr__()


class ResearchPapers:
    
    def __init__(self, metadata: pd.DataFrame):
        self.metadata = metadata
        
    def __getitem__(self, item):
        return Paper(self.metadata.iloc[item])
    
    def __len__(self):
        return len(self.metadata)
    
    def head(self, n):
        return ResearchPapers(self.metadata.head(n).copy().reset_index(drop=True))
    
    def tail(self, n):
        return ResearchPapers(self.metadata.tail(n).copy().reset_index(drop=True))
    
    def abstracts(self):
        return self.metadata.abstract.dropna()
    
    def titles(self):
        return self.metadata.title.dropna()
        
    def _repr_html_(self):
        return self.metadata._repr_html_()
    
class Paper:
    
    '''
    A single research paper
    '''
    def __init__(self, item):
        self.paper = item.to_frame().fillna('')
        self.paper.columns = ['Value']
    
    def doi(self):
        return self.paper.loc['doi'].values[0]
    
    def html(self):
        '''
        Load the paper from doi.org and display as HTML. Requires internet to be ON
        '''
        text = get(self.doi())
        return widgets.HTML(text)
    
    def text(self):
        '''
        Load the paper from doi.org and display as text. Requires Internet to be ON
        '''
        text = get(self.doi())
        return text
    
    def abstract(self):
        return self.paper.loc['abstract'].values[0]
    
    def title(self):
        return self.paper.loc['title'].values[0]
    
    def authors(self, split=False):
        '''
        Get a list of authors
        '''
        authors = self.paper.loc['authors'].values[0]
        if not authors:
            return []
        if not split:
            return authors
        if authors.startswith('['):
            authors = authors.lstrip('[').rstrip(']')
            return [a.strip().replace("\'", "") for a in authors.split("\',")]
        
        # Todo: Handle cases where author names are separated by ","
        return [a.strip() for a in authors.split(';')]
        
    def _repr_html_(self):
        return self.paper._repr_html_()
    
papers = ResearchPapers(metadata)

'''
SEARCH INDEX
'''
from rank_bm25 import BM25Okapi
# nltk.download('stopwords')
english_stopwords = list(set(stopwords.words('english')))

def strip_characters(text):
    t = re.sub('\(|\)|:|,|;|\.|’|”|“|\?|%|>|<', '', text)
    t = re.sub('/', ' ', t)
    t = t.replace("'",'')
    return t

def clean(text):
    t = text.lower()
    t = strip_characters(t)
    return t

def tokenize(text):
    words = nltk.word_tokenize(text)
    return list(set([word for word in words 
                     if len(word) > 1
                     and not word in english_stopwords
                     and not (word.isnumeric() and len(word) is not 4)
                     and (not word.isnumeric() or word.isalpha())] )
               )

def preprocess(text):
    t = clean(text)
    tokens = tokenize(t)
    return tokens

class SearchResults:
    
    def __init__(self, 
                 data: pd.DataFrame,
                 columns = None):
        self.results = data
        if columns:
            self.results = self.results[columns]
            
    def __getitem__(self, item):
        return Paper(self.results.loc[item])
    
    def __len__(self):
        return len(self.results)
        
    def _repr_html_(self):
        return self.results._repr_html_()

SEARCH_DISPLAY_COLUMNS = ['title', 'abstract', 'doi', 'authors', 'journal']

class WordTokenIndex:
    
    def __init__(self, 
                 corpus: pd.DataFrame, 
                 columns=SEARCH_DISPLAY_COLUMNS):
        self.corpus = corpus
        raw_search_str = self.corpus.abstract.fillna('') + ' ' + self.corpus.title.fillna('')
        self.index = raw_search_str.apply(preprocess).to_frame()
        self.index.columns = ['terms']
        self.index.index = self.corpus.index
        self.columns = columns
    
    def search(self, search_string):
        search_terms = preprocess(search_string)
        result_index = self.index.terms.apply(lambda terms: any(i in terms for i in search_terms))
        results = self.corpus[result_index].copy().reset_index().rename(columns={'index':'paper'})
        return SearchResults(results, self.columns + ['paper'])

'''
RANK SEARCH INDEX CLASS
'''

class RankBM25Index(WordTokenIndex):
    
    def __init__(self, corpus: pd.DataFrame, columns=SEARCH_DISPLAY_COLUMNS):
        super().__init__(corpus, columns)
        self.bm25 = BM25Okapi(self.index.terms.tolist())
        
    def search(self, search_string, n=10):
        search_terms = preprocess(search_string)
        doc_scores = self.bm25.get_scores(search_terms)
        ind = np.argsort(doc_scores)[::-1][:n]
        results = self.corpus.iloc[ind][self.columns]
        results['Score'] = doc_scores[ind]
        results = results[results.Score > 0]
        return SearchResults(results.reset_index(), self.columns + ['Score'])

'''
CREATE INDEX
'''
print("Creating index...")
bm25_index = RankBM25Index(metadata.head(len(metadata)))

'''
SAVING FILE
'''

import pickle
import datetime
now = datetime.datetime.now()

file_name='index'+now.strftime('%Y%m%d%H%M')+'.pickle'

print("saving index file...: "+file_name)

with open(file_name, 'wb') as f:
    pickle.dump(bm25_index, f)

print("saved file: "+file_name)

