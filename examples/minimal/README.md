# A minimal bizdash deployment

A deployment is a repository holding only what is its own:

```
site/
  bizdash.config.json            the property, OAuth client, events, funnel, traffic rule
  cards/signups-by-landing.js    a plugin card
.github/workflows/
  deploy.yml                     push to main -> GitHub Pages, pinned to a bizdash release
  preview.yml                    a preview per pull request
```

Try it locally with synthetic data (the `demo` section of the config shapes it):

```sh
pip install "git+https://github.com/pablo-mayrgundter/bizdash@v0.1.0"
python -m bizdash serve --demo --site site        # http://127.0.0.1:5077
```

Or assemble the static site exactly as the workflow does and serve it with
anything:

```sh
python -m bizdash build --site site --out _site
python -m http.server -d _site 8080                # http://localhost:8080/?demo=1
```

To go live, set `propertyId` and `clientId` in `site/bizdash.config.json`
(see the bizdash README, "Auth"), enable GitHub Pages on the `gh-pages`
branch, and push. To upgrade, change the two `v0.1.0` pins in each workflow.
