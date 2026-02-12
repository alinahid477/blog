

## Useful links

https://janus-idp.io/plugins


https://github.com/redhat-developer/red-hat-developer-hub-software-templates/tree/main

https://github.com/idp-team/software-templates/blob/master/scaffolder-templates/github-push-template/template.yaml

https://piotrminkowski.com/2024/07/04/idp-on-openshift-with-red-hat-developer-hub/

https://docs.redhat.com/en/documentation/red_hat_developer_hub/1.3/html-single/administration_guide_for_red_hat_developer_hub/index#proc-add-custom-app-config-file-ocp-operator_admin-rhdh

https://docs.redhat.com/en/documentation/red_hat_developer_hub/1.3/html/authentication/assembly-auth-provider-github#enabling-authentication-with-github


https://github.com/janus-idp/backstage-showcase/blob/main/catalog-entities/all.yaml


Launch Ansible Job: https://github.com/redhat-developer/red-hat-developer-hub-software-templates/blob/main/templates/github/launch-ansible-job/README.md



latest:

https://developers.redhat.com/articles/2026/02/02/how-developer-hub-simplifies-backstage-configuration#importing_static_catalogs



```
kubectl rollout restart deployment/backstage-selfserve-portal -n backstage

oc logs deployment/backstage-selfserve-portal -c backstage-backend -n backstage --tail=200 | grep -iE "error|warn|fail"
```