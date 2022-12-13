# Pre-release Tests

1. Create a new environment. For instance, using conda:
```
conda create --name fresh_env python=3.9
```
2. Activate the new environment
```
conda activate fresh_env
```
3. Clone the dev version of SpeechBrain
https://github.com/speechbrain/speechbrain

4. Install the extra-dependencies
```
cd speechbrain
pip install -r requirements.txt
```
5. Install SpeechBrain
```
pip install -e .
```
6. Install all recipe extra-dependencies (check for latest/fixed versions)
```
find recipes | grep extra | xargs cat | sort -u | grep -v \# | xargs -I {} pip install {}
pip install fairseq
```
7. Run the basic tests by typing:
```
pytest
```
8. Run load yaml test:
```
tests/.run-load-yaml-tests.sh
```
9. Run recipe tests
```
tests/.run-recipe-tests.sh
```
10. Make sure all Huggingface repos are working
```
tests/.run-HF-checks.sh
```
11. Check URLs
```
tests/.run-url-checks.sh
```

Make sure all the tests are passing. Also, make sure to check that the tutorials are working (we might set up an automatic test for that as well in the future).
